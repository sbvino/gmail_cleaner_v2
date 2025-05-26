"""
Gmail Cleaner Flask Application
Production-ready web interface with API endpoints
"""

import os
import json
import logging
import secrets
from datetime import datetime, timedelta
from functools import wraps
from typing import Dict, List, Optional

from flask import Flask, render_template, request, jsonify, session, send_file, Response
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_compress import Compress
from werkzeug.security import generate_password_hash, check_password_hash
import redis
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from analyzer import GmailAnalyzer, EmailMetadata, SenderStats

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max request size

# Initialize extensions
CORS(app, origins=['http://localhost:*', 'https://localhost:*'])
Compress(app)
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri=f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', 6379)}"
)

# Initialize Gmail analyzer
analyzer = GmailAnalyzer()

# Initialize scheduler for automated tasks
scheduler = BackgroundScheduler()
scheduler.start()

# Global state for progress tracking
progress_state = {
    'current_operation': None,
    'progress': 0,
    'total': 0,
    'message': '',
    'start_time': None
}

# Redis client for real-time updates
try:
    redis_client = redis.Redis(
        host=os.getenv('REDIS_HOST', 'localhost'),
        port=int(os.getenv('REDIS_PORT', 6379)),
        decode_responses=True
    )
    redis_client.ping()
except Exception as e:
    logger.warning(f"Redis not available: {e}")
    redis_client = None


def update_progress(current: int, total: int, message: str = ''):
    """Update operation progress"""
    global progress_state
    progress_state.update({
        'progress': current,
        'total': total,
        'message': message,
        'percentage': (current / total * 100) if total > 0 else 0
    })
    
    # Publish to Redis for real-time updates
    if redis_client:
        redis_client.publish('progress', json.dumps(progress_state))


def require_auth(f):
    """Decorator to require authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function


def validate_csrf(f):
    """Decorator to validate CSRF token"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
        if not token or token != session.get('csrf_token'):
            return jsonify({'error': 'Invalid CSRF token'}), 403
        return f(*args, **kwargs)
    return decorated_function


@app.before_request
def before_request():
    """Initialize session and CSRF token"""
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    
    # Check if analyzer is authenticated
    if not hasattr(analyzer, 'service') or analyzer.service is None:
        if request.endpoint and request.endpoint.startswith('api_'):
            try:
                analyzer.authenticate()
                session['authenticated'] = True
            except Exception as e:
                logger.error(f"Authentication failed: {e}")


@app.errorhandler(Exception)
def handle_error(error):
    """Global error handler"""
    logger.error(f"Unhandled error: {error}", exc_info=True)
    return jsonify({
        'error': 'Internal server error',
        'message': str(error) if app.debug else 'An error occurred'
    }), 500


@app.route('/')
def index():
    """Main dashboard"""
    return render_template('index.html', csrf_token=session.get('csrf_token'))


@app.route('/api/auth/status')
def auth_status():
    """Check authentication status"""
    return jsonify({
        'authenticated': session.get('authenticated', False),
        'csrf_token': session.get('csrf_token')
    })


@app.route('/api/auth/login', methods=['POST'])
@limiter.limit("5 per minute")
def auth_login():
    """Authenticate with Gmail"""
    try:
        success = analyzer.authenticate()
        if success:
            session['authenticated'] = True
            session.permanent = True
            return jsonify({'success': True, 'message': 'Authenticated successfully'})
        else:
            return jsonify({'success': False, 'message': 'Authentication failed'}), 401
    except Exception as e:
        logger.error(f"Authentication error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/analyze/senders', methods=['GET'])
@require_auth
@limiter.limit("10 per minute")
def analyze_senders():
    """Get sender statistics"""
    try:
        # Get parameters
        max_results = int(request.args.get('max_results', 1000))
        use_cache = request.args.get('use_cache', 'true').lower() == 'true'
        query = request.args.get('query', '')
        
        # Update progress
        update_progress(0, 100, 'Fetching emails...')
        
        # Fetch emails
        emails = analyzer.fetch_emails_batch(query=query, max_results=max_results)
        
        update_progress(50, 100, 'Analyzing senders...')
        
        # Analyze senders
        senders = analyzer.analyze_senders(emails)
        
        # Convert to list and sort
        sender_list = []
        for email, stats in senders.items():
            sender_list.append({
                'email': stats.email,
                'domain': stats.domain,
                'total_count': stats.total_count,
                'unread_count': stats.unread_count,
                'total_size': stats.total_size,
                'size_mb': round(stats.total_size / (1024 * 1024), 2),
                'oldest_date': stats.oldest_date.isoformat() if stats.oldest_date else None,
                'newest_date': stats.newest_date.isoformat() if stats.newest_date else None,
                'email_velocity': round(stats.email_velocity, 2),
                'spam_score': round(stats.spam_score, 2),
                'is_newsletter': stats.is_newsletter,
                'is_automated': stats.is_automated,
                'has_unsubscribe': stats.has_unsubscribe,
                'attachment_count': stats.attachment_count,
                'subject_patterns': stats.subject_patterns
            })
        
        # Sort by total count descending
        sender_list.sort(key=lambda x: x['total_count'], reverse=True)
        
        update_progress(100, 100, 'Analysis complete')
        
        return jsonify({
            'success': True,
            'data': {
                'senders': sender_list[:100],  # Top 100 senders
                'total_senders': len(senders),
                'total_emails': len(emails),
                'timestamp': datetime.now().isoformat()
            }
        })
        
    except Exception as e:
        logger.error(f"Error analyzing senders: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/analyze/domains', methods=['GET'])
@require_auth
def analyze_domains():
    """Get domain statistics"""
    try:
        max_results = int(request.args.get('max_results', 1000))
        
        # Fetch emails
        emails = analyzer.fetch_emails_batch(max_results=max_results)
        
        # Get domain stats
        domains = analyzer.get_domain_stats(emails)
        
        # Convert to list and sort
        domain_list = []
        for domain, stats in domains.items():
            if domain:  # Skip empty domains
                domain_list.append({
                    'domain': domain,
                    'count': stats['count'],
                    'unread': stats['unread'],
                    'size_mb': round(stats['size_mb'], 2),
                    'unique_senders': stats['unique_senders'],
                    'oldest': stats['oldest'].isoformat() if stats['oldest'] else None,
                    'newest': stats['newest'].isoformat() if stats['newest'] else None
                })
        
        domain_list.sort(key=lambda x: x['count'], reverse=True)
        
        return jsonify({
            'success': True,
            'data': {
                'domains': domain_list[:50],  # Top 50 domains
                'total_domains': len(domains)
            }
        })
        
    except Exception as e:
        logger.error(f"Error analyzing domains: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/analyze/threads', methods=['GET'])
@require_auth
def analyze_threads():
    """Analyze email threads"""
    try:
        # This would require additional implementation in the analyzer
        # For now, return a placeholder
        return jsonify({
            'success': True,
            'data': {
                'message': 'Thread analysis not yet implemented',
                'threads': []
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/delete/sender', methods=['POST'])
@require_auth
@validate_csrf
@limiter.limit("5 per minute")
def delete_by_sender():
    """Delete all emails from a specific sender"""
    try:
        data = request.get_json()
        sender = data.get('sender')
        dry_run = data.get('dry_run', False)
        
        if not sender:
            return jsonify({'success': False, 'error': 'Sender email required'}), 400
        
        # Set operation state
        progress_state['current_operation'] = f"Deleting emails from {sender}"
        progress_state['start_time'] = datetime.now()
        
        # Delete emails
        result = analyzer.delete_emails_by_sender(sender, dry_run=dry_run)
        
        # Clear operation state
        progress_state['current_operation'] = None
        
        return jsonify({
            'success': result['success'],
            'message': result['message'],
            'deleted_count': result['count'],
            'failed_count': result.get('failed', 0)
        })
        
    except Exception as e:
        logger.error(f"Error deleting by sender: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/delete/bulk', methods=['POST'])
@require_auth
@validate_csrf
@limiter.limit("5 per minute")
def delete_bulk():
    """Bulk delete emails by criteria"""
    try:
        criteria = request.get_json()
        dry_run = criteria.pop('dry_run', False)
        
        # Validate criteria
        valid_criteria = {
            'sender', 'domain', 'older_than_days', 'has_attachment',
            'is_unread', 'min_size_mb', 'exclude_important', 'exclude_starred'
        }
        
        filtered_criteria = {
            k: v for k, v in criteria.items() 
            if k in valid_criteria and v is not None
        }
        
        if not filtered_criteria:
            return jsonify({'success': False, 'error': 'No valid criteria provided'}), 400
        
        # Set operation state
        progress_state['current_operation'] = "Bulk delete operation"
        progress_state['start_time'] = datetime.now()
        
        # Delete emails
        result = analyzer.delete_by_criteria(filtered_criteria, dry_run=dry_run)
        
        # Clear operation state
        progress_state['current_operation'] = None
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error in bulk delete: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/delete/thread', methods=['POST'])
@require_auth
@validate_csrf
def delete_thread():
    """Delete an entire email thread"""
    try:
        data = request.get_json()
        thread_id = data.get('thread_id')
        
        if not thread_id:
            return jsonify({'success': False, 'error': 'Thread ID required'}), 400
        
        # This would require additional implementation
        return jsonify({
            'success': False,
            'message': 'Thread deletion not yet implemented'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/suggestions', methods=['GET'])
@require_auth
def get_suggestions():
    """Get cleanup suggestions"""
    try:
        # Check cache first
        cache_key = 'suggestions'
        if redis_client:
            cached = redis_client.get(cache_key)
            if cached:
                return jsonify(json.loads(cached))
        
        # Fetch emails and analyze
        emails = analyzer.fetch_emails_batch(max_results=2000)
        senders = analyzer.analyze_senders(emails)
        
        # Get suggestions
        suggestions = analyzer.get_cleanup_suggestions(senders)
        
        # Calculate total impact
        total_impact = {
            'email_count': sum(s['impact']['email_count'] for s in suggestions),
            'size_mb': sum(s['impact']['size_mb'] for s in suggestions),
            'senders': len(suggestions)
        }
        
        result = {
            'success': True,
            'data': {
                'suggestions': suggestions,
                'total_impact': total_impact,
                'generated_at': datetime.now().isoformat()
            }
        }
        
        # Cache result
        if redis_client:
            redis_client.setex(cache_key, 1800, json.dumps(result))  # 30 min cache
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error getting suggestions: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/unsubscribe', methods=['POST'])
@require_auth
@validate_csrf
def unsubscribe():
    """Attempt to unsubscribe from a sender"""
    try:
        data = request.get_json()
        sender = data.get('sender')
        
        if not sender:
            return jsonify({'success': False, 'error': 'Sender required'}), 400
        
        result = analyzer.auto_unsubscribe(sender)
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error unsubscribing: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/export/csv', methods=['GET'])
@require_auth
def export_csv():
    """Export sender statistics as CSV"""
    try:
        # Fetch and analyze emails
        emails = analyzer.fetch_emails_batch(max_results=5000)
        senders = analyzer.analyze_senders(emails)
        
        # Generate CSV
        csv_data = analyzer.export_statistics(senders)
        
        # Create response
        response = Response(
            csv_data,
            mimetype='text/csv',
            headers={
                'Content-Disposition': f'attachment; filename=gmail_stats_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
            }
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Error exporting CSV: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/rules/create', methods=['POST'])
@require_auth
@validate_csrf
def create_rule():
    """Create an auto-cleanup rule"""
    try:
        data = request.get_json()
        
        # Validate rule data
        required_fields = ['name', 'criteria', 'action']
        for field in required_fields:
            if field not in data:
                return jsonify({'success': False, 'error': f'{field} is required'}), 400
        
        # Save rule to database
        conn = analyzer.db_conn
        cursor = conn.execute("""
            INSERT INTO cleanup_rules (name, criteria, action, is_active, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            data['name'],
            json.dumps(data['criteria']),
            data['action'],
            data.get('is_active', True),
            datetime.now()
        ))
        
        rule_id = cursor.lastrowid
        conn.commit()
        
        # Schedule rule if active
        if data.get('is_active') and data.get('schedule'):
            schedule_rule(rule_id, data['schedule'])
        
        return jsonify({
            'success': True,
            'rule_id': rule_id,
            'message': 'Rule created successfully'
        })
        
    except Exception as e:
        logger.error(f"Error creating rule: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/stats/velocity', methods=['GET'])
@require_auth
def email_velocity():
    """Get email velocity statistics"""
    try:
        days = int(request.args.get('days', 30))
        velocity_data = analyzer.get_email_velocity(days)
        
        return jsonify({
            'success': True,
            'data': velocity_data
        })
        
    except Exception as e:
        logger.error(f"Error getting velocity stats: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/progress', methods=['GET'])
@require_auth
def get_progress():
    """Get current operation progress"""
    global progress_state
    
    if progress_state['current_operation']:
        elapsed = (datetime.now() - progress_state['start_time']).total_seconds()
        progress_state['elapsed_seconds'] = elapsed
    
    return jsonify(progress_state)


@app.route('/api/attachments/large', methods=['GET'])
@require_auth
def find_large_attachments():
    """Find emails with large attachments"""
    try:
        min_size_mb = float(request.args.get('min_size_mb', 5.0))
        attachments = analyzer.find_large_attachments(min_size_mb)
        
        return jsonify({
            'success': True,
            'data': {
                'attachments': attachments,
                'total_size_mb': sum(a['size_mb'] for a in attachments)
            }
        })
        
    except Exception as e:
        logger.error(f"Error finding attachments: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/restore', methods=['POST'])
@require_auth
@validate_csrf
def restore_emails():
    """Restore emails from trash"""
    try:
        data = request.get_json()
        email_ids = data.get('email_ids', [])
        
        if not email_ids:
            return jsonify({'success': False, 'error': 'No email IDs provided'}), 400
        
        result = analyzer.restore_emails(email_ids)
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error restoring emails: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/stats/summary', methods=['GET'])
@require_auth
def get_summary_stats():
    """Get summary statistics"""
    try:
        # Get basic stats from database
        conn = analyzer.db_conn
        cursor = conn.execute("""
            SELECT 
                COUNT(DISTINCT sender) as total_senders,
                SUM(total_count) as total_emails,
                SUM(total_size) as total_size,
                AVG(spam_score) as avg_spam_score
            FROM sender_stats
            WHERE last_updated > datetime('now', '-1 day')
        """)
        
        row = cursor.fetchone()
        
        # Get deletion history
        cursor = conn.execute("""
            SELECT COUNT(*) as deleted_count
            FROM email_history
            WHERE deleted_at > datetime('now', '-7 days')
        """)
        
        deleted = cursor.fetchone()
        
        return jsonify({
            'success': True,
            'data': {
                'total_senders': row['total_senders'] or 0,
                'total_emails': row['total_emails'] or 0,
                'total_size_mb': round((row['total_size'] or 0) / (1024 * 1024), 2),
                'avg_spam_score': round(row['avg_spam_score'] or 0, 2),
                'deleted_last_week': deleted['deleted_count'] or 0
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting summary stats: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def schedule_rule(rule_id: int, schedule: Dict):
    """Schedule a cleanup rule"""
    try:
        # Create job function
        def run_rule():
            conn = analyzer.db_conn
            cursor = conn.execute(
                "SELECT * FROM cleanup_rules WHERE id = ? AND is_active = 1",
                (rule_id,)
            )
            rule = cursor.fetchone()
            
            if rule:
                criteria = json.loads(rule['criteria'])
                logger.info(f"Running scheduled rule: {rule['name']}")
                
                # Execute cleanup
                result = analyzer.delete_by_criteria(criteria, dry_run=False)
                logger.info(f"Rule {rule['name']} completed: {result}")
        
        # Add job to scheduler
        if schedule['type'] == 'cron':
            trigger = CronTrigger(
                hour=schedule.get('hour', 0),
                minute=schedule.get('minute', 0),
                day_of_week=schedule.get('day_of_week', '*')
            )
        else:  # interval
            trigger = 'interval'
            kwargs = {schedule['interval_type']: schedule['interval_value']}
        
        scheduler.add_job(
            run_rule,
            trigger=trigger,
            id=f"rule_{rule_id}",
            replace_existing=True
        )
        
        logger.info(f"Scheduled rule {rule_id}")
        
    except Exception as e:
        logger.error(f"Error scheduling rule: {e}")


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        # Check Gmail API
        gmail_ok = hasattr(analyzer, 'service') and analyzer.service is not None
        
        # Check Redis
        redis_ok = False
        if redis_client:
            try:
                redis_client.ping()
                redis_ok = True
            except:
                pass
        
        # Check database
        db_ok = False
        try:
            analyzer.db_conn.execute("SELECT 1").fetchone()
            db_ok = True
        except:
            pass
        
        status = 'healthy' if gmail_ok and db_ok else 'degraded'
        
        return jsonify({
            'status': status,
            'services': {
                'gmail_api': 'up' if gmail_ok else 'down',
                'redis': 'up' if redis_ok else 'down',
                'database': 'up' if db_ok else 'down'
            },
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 500


if __name__ == '__main__':
    # Development server
    app.run(
        host='0.0.0.0',
        port=int(os.getenv('PORT', 5000)),
        debug=os.getenv('FLASK_ENV') == 'development',
        threaded=True
    )
