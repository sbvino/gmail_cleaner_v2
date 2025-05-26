"""
Gmail Email Analyzer with Bulk Operations
Optimized for performance with batch processing
"""

import os
import re
import logging
import time
import base64
import pickle
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Tuple, Optional, Set
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import redis
import sqlite3
from transformers import pipeline
import yaml

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Gmail API scope
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

@dataclass
class EmailMetadata:
    """Lightweight email metadata for fast processing"""
    id: str
    thread_id: str
    sender: str
    sender_domain: str
    subject: str
    date: datetime
    size: int
    is_read: bool
    has_attachments: bool
    labels: Set[str] = field(default_factory=set)
    snippet: str = ""
    
    @property
    def age_days(self) -> int:
        return (datetime.now() - self.date).days

@dataclass
class SenderStats:
    """Statistics for each sender"""
    email: str
    domain: str
    total_count: int = 0
    unread_count: int = 0
    total_size: int = 0
    oldest_date: Optional[datetime] = None
    newest_date: Optional[datetime] = None
    subject_patterns: Dict[str, int] = field(default_factory=dict)
    thread_ids: Set[str] = field(default_factory=set)
    email_ids: List[str] = field(default_factory=list)
    is_newsletter: bool = False
    is_automated: bool = False
    spam_score: float = 0.0
    has_unsubscribe: bool = False
    attachment_count: int = 0
    
    @property
    def average_size(self) -> int:
        return self.total_size // self.total_count if self.total_count > 0 else 0
    
    @property
    def email_velocity(self) -> float:
        """Emails per day"""
        if self.oldest_date and self.newest_date:
            days = max((self.newest_date - self.oldest_date).days, 1)
            return self.total_count / days
        return 0.0

class GmailAnalyzer:
    """Enhanced Gmail analyzer with bulk operations and caching"""
    
    def __init__(self, cache_ttl: int = 3600):
        self.service = None
        self.cache_ttl = cache_ttl
        self.batch_size = 100  # Gmail API batch size
        self.redis_client = None
        self.db_conn = None
        self.spam_classifier = None
        self.patterns = None
        self.trusted_domains = None
        
        # Initialize components
        self._init_cache()
        self._init_database()
        self._init_patterns()
        self._init_spam_classifier()
        
    def _init_cache(self):
        """Initialize Redis cache"""
        try:
            self.redis_client = redis.Redis(
                host=os.getenv('REDIS_HOST', 'localhost'),
                port=int(os.getenv('REDIS_PORT', 6379)),
                decode_responses=True
            )
            self.redis_client.ping()
            logger.info("Redis cache initialized")
        except Exception as e:
            logger.warning(f"Redis not available, continuing without cache: {e}")
            self.redis_client = None
    
    def _init_database(self):
        """Initialize SQLite database"""
        db_path = os.getenv('DB_PATH', 'data/gmail_cleaner.db')
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
        self.db_conn = sqlite3.connect(db_path, check_same_thread=False)
        self.db_conn.row_factory = sqlite3.Row
        
        # Create tables
        self.db_conn.executescript("""
            CREATE TABLE IF NOT EXISTS email_history (
                email_id TEXT PRIMARY KEY,
                sender TEXT,
                subject TEXT,
                deleted_at TIMESTAMP,
                can_restore_until TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS sender_stats (
                sender TEXT PRIMARY KEY,
                domain TEXT,
                total_count INTEGER,
                unread_count INTEGER,
                total_size INTEGER,
                is_newsletter BOOLEAN,
                is_automated BOOLEAN,
                spam_score REAL,
                last_updated TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS cleanup_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                criteria TEXT,
                action TEXT,
                is_active BOOLEAN,
                created_at TIMESTAMP
            );
            
            CREATE INDEX IF NOT EXISTS idx_sender ON email_history(sender);
            CREATE INDEX IF NOT EXISTS idx_deleted_at ON email_history(deleted_at);
        """)
        self.db_conn.commit()
        logger.info("Database initialized")
    
    def _init_patterns(self):
        """Load spam patterns and trusted domains"""
        patterns_file = 'config/patterns.yaml'
        if os.path.exists(patterns_file):
            with open(patterns_file, 'r') as f:
                config = yaml.safe_load(f)
                self.patterns = config.get('spam_patterns', [])
                self.trusted_domains = set(config.get('trusted_domains', []))
        else:
            # Default patterns
            self.patterns = [
                r'unsubscribe',
                r'click here',
                r'limited time',
                r'act now',
                r'congratulations',
                r'winner',
                r'free gift',
                r'no obligation'
            ]
            self.trusted_domains = {
                'gmail.com', 'google.com', 'microsoft.com', 
                'apple.com', 'amazon.com', 'github.com'
            }
    
    def _init_spam_classifier(self):
        """Initialize local spam classifier"""
        try:
            # Use a lightweight model for RPi compatibility
            self.spam_classifier = pipeline(
                "text-classification",
                model="distilbert-base-uncased-finetuned-sst-2-english",
                device=-1  # CPU only
            )
            logger.info("Spam classifier initialized")
        except Exception as e:
            logger.warning(f"Could not load spam classifier: {e}")
            self.spam_classifier = None
    
    def authenticate(self, credentials_file: str = 'credentials.json'):
        """Authenticate with Gmail API"""
        creds = None
        token_file = 'token.pickle'
        
        if os.path.exists(token_file):
            with open(token_file, 'rb') as token:
                creds = pickle.load(token)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    credentials_file, SCOPES)
                creds = flow.run_local_server(port=0)
            
            with open(token_file, 'wb') as token:
                pickle.dump(creds, token)
        
        self.service = build('gmail', 'v1', credentials=creds)
        logger.info("Gmail API authenticated")
        return True
    
    def _get_cache_key(self, key_type: str, identifier: str) -> str:
        """Generate cache key"""
        return f"gmail:{key_type}:{identifier}"
    
    def _get_from_cache(self, key: str) -> Optional[Dict]:
        """Get data from cache"""
        if not self.redis_client:
            return None
        
        try:
            data = self.redis_client.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error(f"Cache read error: {e}")
        
        return None
    
    def _set_cache(self, key: str, data: Dict, ttl: Optional[int] = None):
        """Set data in cache"""
        if not self.redis_client:
            return
        
        try:
            self.redis_client.setex(
                key,
                ttl or self.cache_ttl,
                json.dumps(data, default=str)
            )
        except Exception as e:
            logger.error(f"Cache write error: {e}")
    
    def fetch_emails_batch(self, query: str = '', max_results: int = 1000) -> List[EmailMetadata]:
        """Fetch emails in batches for performance"""
        emails = []
        page_token = None
        
        # Check cache first
        cache_key = self._get_cache_key('emails', hashlib.md5(query.encode()).hexdigest())
        cached = self._get_from_cache(cache_key)
        if cached:
            logger.info("Using cached email list")
            return [EmailMetadata(**e) for e in cached]
        
        logger.info(f"Fetching emails with query: {query or 'all'}")
        
        try:
            while len(emails) < max_results:
                # Fetch message IDs
                result = self.service.users().messages().list(
                    userId='me',
                    q=query,
                    pageToken=page_token,
                    maxResults=min(self.batch_size, max_results - len(emails))
                ).execute()
                
                messages = result.get('messages', [])
                if not messages:
                    break
                
                # Batch fetch message details
                batch_emails = self._batch_fetch_details(messages)
                emails.extend(batch_emails)
                
                page_token = result.get('nextPageToken')
                if not page_token:
                    break
                
                logger.info(f"Fetched {len(emails)} emails so far...")
        
        except HttpError as e:
            logger.error(f"Gmail API error: {e}")
            raise
        
        # Cache results
        self._set_cache(cache_key, [e.__dict__ for e in emails])
        
        logger.info(f"Total emails fetched: {len(emails)}")
        return emails
    
    def _batch_fetch_details(self, messages: List[Dict]) -> List[EmailMetadata]:
        """Fetch email details in parallel batches"""
        emails = []
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_msg = {
                executor.submit(self._fetch_single_email, msg['id']): msg 
                for msg in messages
            }
            
            for future in as_completed(future_to_msg):
                try:
                    email = future.result()
                    if email:
                        emails.append(email)
                except Exception as e:
                    logger.error(f"Error fetching email: {e}")
        
        return emails
    
    def _fetch_single_email(self, email_id: str) -> Optional[EmailMetadata]:
        """Fetch single email metadata"""
        try:
            msg = self.service.users().messages().get(
                userId='me',
                id=email_id,
                format='metadata',
                metadataHeaders=['From', 'Subject', 'Date']
            ).execute()
            
            # Extract metadata
            headers = {h['name']: h['value'] for h in msg['payload'].get('headers', [])}
            
            # Parse sender
            from_header = headers.get('From', '')
            sender_match = re.search(r'<(.+?)>', from_header)
            sender = sender_match.group(1) if sender_match else from_header
            sender_domain = sender.split('@')[-1] if '@' in sender else ''
            
            # Parse date
            date_str = headers.get('Date', '')
            try:
                date = datetime.strptime(date_str[:31], '%a, %d %b %Y %H:%M:%S')
            except:
                date = datetime.now()
            
            # Check for attachments
            has_attachments = self._has_attachments(msg['payload'])
            
            return EmailMetadata(
                id=email_id,
                thread_id=msg['threadId'],
                sender=sender.lower(),
                sender_domain=sender_domain.lower(),
                subject=headers.get('Subject', ''),
                date=date,
                size=int(msg.get('sizeEstimate', 0)),
                is_read='UNREAD' not in msg.get('labelIds', []),
                has_attachments=has_attachments,
                labels=set(msg.get('labelIds', [])),
                snippet=msg.get('snippet', '')
            )
            
        except Exception as e:
            logger.error(f"Error fetching email {email_id}: {e}")
            return None
    
    def _has_attachments(self, payload: Dict) -> bool:
        """Check if email has attachments"""
        if 'parts' in payload:
            for part in payload['parts']:
                if part.get('filename'):
                    return True
                if 'parts' in part:
                    if self._has_attachments(part):
                        return True
        return False
    
    def analyze_senders(self, emails: List[EmailMetadata]) -> Dict[str, SenderStats]:
        """Analyze emails grouped by sender"""
        logger.info("Analyzing senders...")
        
        senders = defaultdict(lambda: SenderStats('', ''))
        
        for email in emails:
            sender = email.sender
            stats = senders[sender]
            
            # Initialize sender stats
            if stats.email == '':
                stats.email = sender
                stats.domain = email.sender_domain
            
            # Update counts
            stats.total_count += 1
            stats.total_size += email.size
            stats.thread_ids.add(email.thread_id)
            stats.email_ids.append(email.id)
            
            if not email.is_read:
                stats.unread_count += 1
            
            if email.has_attachments:
                stats.attachment_count += 1
            
            # Update dates
            if not stats.oldest_date or email.date < stats.oldest_date:
                stats.oldest_date = email.date
            if not stats.newest_date or email.date > stats.newest_date:
                stats.newest_date = email.date
            
            # Analyze subject patterns
            subject_lower = email.subject.lower()
            for pattern in ['re:', 'fwd:', 'newsletter', 'unsubscribe', 'invoice', 'receipt']:
                if pattern in subject_lower:
                    stats.subject_patterns[pattern] = stats.subject_patterns.get(pattern, 0) + 1
            
            # Check for newsletter/automated indicators
            if any(word in subject_lower for word in ['newsletter', 'update', 'digest', 'weekly', 'monthly']):
                stats.is_newsletter = True
            
            if any(word in sender for word in ['noreply', 'no-reply', 'notification', 'automated']):
                stats.is_automated = True
            
            if 'unsubscribe' in email.snippet.lower():
                stats.has_unsubscribe = True
        
        # Calculate spam scores
        for sender, stats in senders.items():
            stats.spam_score = self._calculate_spam_score(stats)
        
        # Save to database
        self._save_sender_stats(senders)
        
        logger.info(f"Analyzed {len(senders)} unique senders")
        return dict(senders)
    
    def _calculate_spam_score(self, stats: SenderStats) -> float:
        """Calculate spam score for a sender (0-1)"""
        score = 0.0
        
        # High email velocity
        if stats.email_velocity > 1:  # More than 1 per day
            score += 0.2
        
        # Newsletter indicators
        if stats.is_newsletter:
            score += 0.3
        
        # Automated sender
        if stats.is_automated:
            score += 0.2
        
        # Has unsubscribe links
        if stats.has_unsubscribe:
            score += 0.2
        
        # Low read rate
        read_rate = 1 - (stats.unread_count / stats.total_count)
        if read_rate < 0.3:  # Less than 30% read
            score += 0.3
        
        # Subject patterns
        spam_patterns = stats.subject_patterns.get('unsubscribe', 0)
        if spam_patterns > stats.total_count * 0.5:
            score += 0.2
        
        # Trusted domain bonus
        if stats.domain in self.trusted_domains:
            score *= 0.5
        
        return min(score, 1.0)
    
    def _save_sender_stats(self, senders: Dict[str, SenderStats]):
        """Save sender statistics to database"""
        try:
            for sender, stats in senders.items():
                self.db_conn.execute("""
                    INSERT OR REPLACE INTO sender_stats 
                    (sender, domain, total_count, unread_count, total_size, 
                     is_newsletter, is_automated, spam_score, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    stats.email, stats.domain, stats.total_count,
                    stats.unread_count, stats.total_size,
                    stats.is_newsletter, stats.is_automated,
                    stats.spam_score, datetime.now()
                ))
            
            self.db_conn.commit()
        except Exception as e:
            logger.error(f"Error saving sender stats: {e}")
    
    def get_cleanup_suggestions(self, senders: Dict[str, SenderStats]) -> List[Dict]:
        """Generate smart cleanup suggestions"""
        suggestions = []
        
        # Sort senders by spam score and email count
        sorted_senders = sorted(
            senders.values(),
            key=lambda s: (s.spam_score, s.total_count),
            reverse=True
        )
        
        for stats in sorted_senders[:20]:  # Top 20 suggestions
            # Skip important domains
            if stats.domain in self.trusted_domains and stats.spam_score < 0.7:
                continue
            
            # Skip if too few emails
            if stats.total_count < 5:
                continue
            
            suggestion = {
                'sender': stats.email,
                'domain': stats.domain,
                'reason': self._get_suggestion_reason(stats),
                'impact': {
                    'email_count': stats.total_count,
                    'size_mb': stats.total_size / (1024 * 1024),
                    'unread_count': stats.unread_count
                },
                'confidence': stats.spam_score,
                'action': 'delete' if stats.spam_score > 0.7 else 'review'
            }
            
            suggestions.append(suggestion)
        
        return suggestions
    
    def _get_suggestion_reason(self, stats: SenderStats) -> str:
        """Generate human-readable suggestion reason"""
        reasons = []
        
        if stats.is_newsletter:
            reasons.append("Newsletter")
        
        if stats.is_automated:
            reasons.append("Automated sender")
        
        if stats.email_velocity > 1:
            reasons.append(f"High volume ({stats.email_velocity:.1f} emails/day)")
        
        read_rate = 1 - (stats.unread_count / stats.total_count)
        if read_rate < 0.3:
            reasons.append(f"Low read rate ({read_rate*100:.0f}%)")
        
        if stats.has_unsubscribe:
            reasons.append("Marketing email")
        
        return " - ".join(reasons) if reasons else "Potential spam"
    
    def delete_emails_by_sender(self, sender: str, dry_run: bool = False) -> Dict:
        """Delete all emails from a specific sender"""
        logger.info(f"{'[DRY RUN] ' if dry_run else ''}Deleting emails from: {sender}")
        
        # Get all emails from sender
        emails = self.fetch_emails_batch(f'from:{sender}')
        
        if not emails:
            return {'success': False, 'message': 'No emails found', 'count': 0}
        
        # Batch delete
        deleted_count = 0
        failed_count = 0
        
        for batch_start in range(0, len(emails), self.batch_size):
            batch = emails[batch_start:batch_start + self.batch_size]
            email_ids = [e.id for e in batch]
            
            if dry_run:
                deleted_count += len(email_ids)
                logger.info(f"[DRY RUN] Would delete {len(email_ids)} emails")
            else:
                success = self._batch_delete_emails(email_ids)
                if success:
                    deleted_count += len(email_ids)
                    # Save to undo buffer
                    self._save_delete_history(batch)
                else:
                    failed_count += len(email_ids)
        
        # Clear cache
        self._clear_sender_cache(sender)
        
        return {
            'success': True,
            'message': f"{'Would delete' if dry_run else 'Deleted'} {deleted_count} emails",
            'count': deleted_count,
            'failed': failed_count
        }
    
    def _batch_delete_emails(self, email_ids: List[str]) -> bool:
        """Delete emails in batch by moving to trash"""
        try:
            body = {
                'ids': email_ids,
                'removeLabelIds': [],
                'addLabelIds': ['TRASH']
            }
            
            self.service.users().messages().batchModify(
                userId='me',
                body=body
            ).execute()
            
            logger.info(f"Moved {len(email_ids)} emails to trash")
            return True
            
        except HttpError as e:
            logger.error(f"Error deleting emails: {e}")
            return False
    
    def _save_delete_history(self, emails: List[EmailMetadata]):
        """Save deleted emails for undo functionality"""
        try:
            for email in emails:
                self.db_conn.execute("""
                    INSERT INTO email_history 
                    (email_id, sender, subject, deleted_at, can_restore_until)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    email.id,
                    email.sender,
                    email.subject,
                    datetime.now(),
                    datetime.now() + timedelta(hours=24)
                ))
            
            self.db_conn.commit()
        except Exception as e:
            logger.error(f"Error saving delete history: {e}")
    
    def _clear_sender_cache(self, sender: str):
        """Clear cache for a specific sender"""
        if self.redis_client:
            pattern = f"gmail:*{sender}*"
            for key in self.redis_client.scan_iter(match=pattern):
                self.redis_client.delete(key)
    
    def restore_emails(self, email_ids: List[str]) -> Dict:
        """Restore emails from trash"""
        try:
            body = {
                'ids': email_ids,
                'removeLabelIds': ['TRASH'],
                'addLabelIds': ['INBOX']
            }
            
            self.service.users().messages().batchModify(
                userId='me',
                body=body
            ).execute()
            
            # Remove from history
            placeholders = ','.join('?' * len(email_ids))
            self.db_conn.execute(
                f"DELETE FROM email_history WHERE email_id IN ({placeholders})",
                email_ids
            )
            self.db_conn.commit()
            
            return {
                'success': True,
                'message': f"Restored {len(email_ids)} emails",
                'count': len(email_ids)
            }
            
        except Exception as e:
            logger.error(f"Error restoring emails: {e}")
            return {
                'success': False,
                'message': str(e),
                'count': 0
            }
    
    def delete_by_criteria(self, criteria: Dict, dry_run: bool = False) -> Dict:
        """Delete emails based on multiple criteria"""
        query_parts = []
        
        # Build query
        if criteria.get('sender'):
            query_parts.append(f"from:{criteria['sender']}")
        
        if criteria.get('domain'):
            query_parts.append(f"from:@{criteria['domain']}")
        
        if criteria.get('older_than_days'):
            date = datetime.now() - timedelta(days=criteria['older_than_days'])
            query_parts.append(f"before:{date.strftime('%Y/%m/%d')}")
        
        if criteria.get('has_attachment'):
            query_parts.append("has:attachment")
        
        if criteria.get('is_unread'):
            query_parts.append("is:unread")
        
        if criteria.get('min_size_mb'):
            size_bytes = criteria['min_size_mb'] * 1024 * 1024
            query_parts.append(f"size:{size_bytes}")
        
        query = ' '.join(query_parts)
        logger.info(f"Delete query: {query}")
        
        # Fetch matching emails
        emails = self.fetch_emails_batch(query)
        
        if not emails:
            return {'success': False, 'message': 'No matching emails found', 'count': 0}
        
        # Apply additional filters if needed
        if criteria.get('exclude_important'):
            emails = [e for e in emails if 'IMPORTANT' not in e.labels]
        
        if criteria.get('exclude_starred'):
            emails = [e for e in emails if 'STARRED' not in e.labels]
        
        # Delete emails
        if dry_run:
            return {
                'success': True,
                'message': f"Would delete {len(emails)} emails",
                'count': len(emails),
                'emails': [
                    {
                        'sender': e.sender,
                        'subject': e.subject,
                        'date': e.date.isoformat(),
                        'size_mb': e.size / (1024 * 1024)
                    }
                    for e in emails[:10]  # Preview first 10
                ]
            }
        else:
            email_ids = [e.id for e in emails]
            deleted = 0
            
            for batch_start in range(0, len(email_ids), self.batch_size):
                batch = email_ids[batch_start:batch_start + self.batch_size]
                if self._batch_delete_emails(batch):
                    deleted += len(batch)
                    self._save_delete_history(
                        emails[batch_start:batch_start + self.batch_size]
                    )
            
            return {
                'success': True,
                'message': f"Deleted {deleted} emails",
                'count': deleted
            }
    
    def get_domain_stats(self, emails: List[EmailMetadata]) -> Dict[str, Dict]:
        """Analyze emails grouped by domain"""
        domains = defaultdict(lambda: {
            'count': 0,
            'unread': 0,
            'size': 0,
            'senders': set(),
            'oldest': None,
            'newest': None
        })
        
        for email in emails:
            domain = email.sender_domain
            stats = domains[domain]
            
            stats['count'] += 1
            stats['size'] += email.size
            stats['senders'].add(email.sender)
            
            if not email.is_read:
                stats['unread'] += 1
            
            if not stats['oldest'] or email.date < stats['oldest']:
                stats['oldest'] = email.date
            if not stats['newest'] or email.date > stats['newest']:
                stats['newest'] = email.date
        
        # Convert sets to counts
        for domain, stats in domains.items():
            stats['unique_senders'] = len(stats['senders'])
            del stats['senders']
            stats['size_mb'] = stats['size'] / (1024 * 1024)
        
        return dict(domains)
    
    def find_large_attachments(self, min_size_mb: float = 5.0) -> List[Dict]:
        """Find emails with large attachments"""
        query = f"has:attachment size:{int(min_size_mb * 1024 * 1024)}"
        emails = self.fetch_emails_batch(query, max_results=100)
        
        results = []
        for email in emails:
            results.append({
                'id': email.id,
                'sender': email.sender,
                'subject': email.subject,
                'date': email.date.isoformat(),
                'size_mb': email.size / (1024 * 1024),
                'age_days': email.age_days
            })
        
        return sorted(results, key=lambda x: x['size_mb'], reverse=True)
    
    def get_email_velocity(self, days: int = 30) -> Dict[str, List]:
        """Calculate email velocity over time"""
        start_date = datetime.now() - timedelta(days=days)
        query = f"after:{start_date.strftime('%Y/%m/%d')}"
        
        emails = self.fetch_emails_batch(query)
        
        # Group by date
        daily_counts = defaultdict(int)
        sender_velocity = defaultdict(lambda: defaultdict(int))
        
        for email in emails:
            date_str = email.date.strftime('%Y-%m-%d')
            daily_counts[date_str] += 1
            sender_velocity[email.sender][date_str] += 1
        
        # Sort senders by total volume
        sender_totals = {
            sender: sum(counts.values())
            for sender, counts in sender_velocity.items()
        }
        top_senders = sorted(
            sender_totals.items(),
            key=lambda x: x[1],
            reverse=True
        )[:10]
        
        return {
            'daily_totals': sorted(daily_counts.items()),
            'top_senders': [
                {
                    'sender': sender,
                    'total': total,
                    'daily': sorted(sender_velocity[sender].items())
                }
                for sender, total in top_senders
            ]
        }
    
    def auto_unsubscribe(self, sender: str) -> Dict:
        """Attempt to unsubscribe from a sender"""
        # First, check if we have unsubscribe info
        emails = self.fetch_emails_batch(f'from:{sender}', max_results=10)
        
        unsubscribe_links = []
        for email in emails:
            # Fetch full email
            try:
                msg = self.service.users().messages().get(
                    userId='me',
                    id=email.id,
                    format='full'
                ).execute()
                
                # Look for unsubscribe headers
                headers = {
                    h['name']: h['value'] 
                    for h in msg['payload'].get('headers', [])
                }
                
                if 'List-Unsubscribe' in headers:
                    unsubscribe_links.append(headers['List-Unsubscribe'])
                
                # Look in email body
                body = self._get_email_body(msg['payload'])
                unsubscribe_urls = re.findall(
                    r'https?://[^\s]+unsubscribe[^\s]*',
                    body,
                    re.IGNORECASE
                )
                unsubscribe_links.extend(unsubscribe_urls)
                
            except Exception as e:
                logger.error(f"Error checking unsubscribe for {email.id}: {e}")
        
        if unsubscribe_links:
            # Clean and deduplicate links
            clean_links = list(set([
                link.strip('<>') 
                for link in unsubscribe_links
            ]))
            
            return {
                'success': True,
                'message': 'Found unsubscribe options',
                'links': clean_links[:3],  # Top 3 unique links
                'action': 'manual'  # User needs to click links
            }
        else:
            return {
                'success': False,
                'message': 'No unsubscribe options found',
                'alternative': 'Consider creating a filter to auto-delete'
            }
    
    def _get_email_body(self, payload: Dict) -> str:
        """Extract email body text"""
        body = ""
        
        if 'parts' in payload:
            for part in payload['parts']:
                if part['mimeType'] == 'text/plain':
                    data = part['body']['data']
                    body += base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
        elif payload['body'].get('data'):
            body = base64.urlsafe_b64decode(
                payload['body']['data']
            ).decode('utf-8', errors='ignore')
        
        return body
    
    def export_statistics(self, senders: Dict[str, SenderStats]) -> bytes:
        """Export sender statistics as CSV"""
        import csv
        import io
        
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=[
            'sender', 'domain', 'total_emails', 'unread_count',
            'total_size_mb', 'oldest_date', 'newest_date',
            'emails_per_day', 'spam_score', 'is_newsletter',
            'is_automated', 'has_unsubscribe'
        ])
        
        writer.writeheader()
        
        for sender, stats in senders.items():
            writer.writerow({
                'sender': stats.email,
                'domain': stats.domain,
                'total_emails': stats.total_count,
                'unread_count': stats.unread_count,
                'total_size_mb': round(stats.total_size / (1024 * 1024), 2),
                'oldest_date': stats.oldest_date.isoformat() if stats.oldest_date else '',
                'newest_date': stats.newest_date.isoformat() if stats.newest_date else '',
                'emails_per_day': round(stats.email_velocity, 2),
                'spam_score': round(stats.spam_score, 2),
                'is_newsletter': stats.is_newsletter,
                'is_automated': stats.is_automated,
                'has_unsubscribe': stats.has_unsubscribe
            })
        
        return output.getvalue().encode('utf-8')


if __name__ == "__main__":
    # Test the analyzer
    analyzer = GmailAnalyzer()
    
    if analyzer.authenticate():
        # Fetch recent emails
        emails = analyzer.fetch_emails_batch(max_results=500)
        
        # Analyze senders
        senders = analyzer.analyze_senders(emails)
        
        # Get suggestions
        suggestions = analyzer.get_cleanup_suggestions(senders)
        
        print(f"\nTop cleanup suggestions:")
        for i, suggestion in enumerate(suggestions[:5], 1):
            print(f"\n{i}. {suggestion['sender']}")
            print(f"   Reason: {suggestion['reason']}")
            print(f"   Impact: {suggestion['impact']['email_count']} emails, "
                  f"{suggestion['impact']['size_mb']:.1f} MB")
            print(f"   Confidence: {suggestion['confidence']:.0%}")
