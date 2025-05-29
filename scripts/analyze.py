#!/usr/bin/env python3
"""
Standalone Gmail Analyzer Script
Run email analysis from the command line without the web interface
"""

import sys
import os
import argparse
import json
from datetime import datetime, timedelta
from tabulate import tabulate

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer import GmailAnalyzer, SenderStats


class Colors:
    """Terminal colors for pretty output"""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def format_size(size_bytes):
    """Format bytes to human readable size"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def print_header(text):
    """Print colored header"""
    print(f"\n{Colors.HEADER}{Colors.BOLD}{text}{Colors.ENDC}")
    print("=" * len(text))


def analyze_inbox(analyzer, args):
    """Perform inbox analysis"""
    print_header("Gmail Inbox Analysis")
    
    # Build query from arguments
    query_parts = []
    if args.query:
        query_parts.append(args.query)
    if args.unread_only:
        query_parts.append("is:unread")
    if args.has_attachment:
        query_parts.append("has:attachment")
    if args.older_than:
        date = datetime.now() - timedelta(days=args.older_than)
        query_parts.append(f"before:{date.strftime('%Y/%m/%d')}")
    if args.newer_than:
        date = datetime.now() - timedelta(days=args.newer_than)
        query_parts.append(f"after:{date.strftime('%Y/%m/%d')}")
    
    query = " ".join(query_parts)
    
    print(f"\nFetching emails (max: {args.max_results})...")
    if query:
        print(f"Query: {query}")
    
    # Fetch emails
    emails = analyzer.fetch_emails_batch(query=query, max_results=args.max_results)
    print(f"Found {len(emails)} emails")
    
    if not emails:
        print(f"{Colors.YELLOW}No emails found matching criteria{Colors.ENDC}")
        return None, None
    
    # Analyze senders
    print("\nAnalyzing senders...")
    senders = analyzer.analyze_senders(emails)
    
    # Calculate summary statistics
    total_size = sum(e.size for e in emails)
    unread_count = sum(1 for e in emails if not e.is_read)
    oldest_date = min(e.date for e in emails)
    newest_date = max(e.date for e in emails)
    
    print(f"\n{Colors.CYAN}Summary Statistics:{Colors.ENDC}")
    print(f"  Total emails: {len(emails)}")
    print(f"  Unique senders: {len(senders)}")
    print(f"  Total size: {format_size(total_size)}")
    print(f"  Unread emails: {unread_count} ({unread_count/len(emails)*100:.1f}%)")
    print(f"  Date range: {oldest_date.strftime('%Y-%m-%d')} to {newest_date.strftime('%Y-%m-%d')}")
    
    return emails, senders


def show_top_senders(senders, limit=20):
    """Display top senders by volume"""
    print_header(f"Top {limit} Senders by Volume")
    
    # Sort senders by total count
    sorted_senders = sorted(senders.values(), key=lambda s: s.total_count, reverse=True)[:limit]
    
    # Prepare table data
    table_data = []
    for i, sender in enumerate(sorted_senders, 1):
        spam_indicator = ""
        if sender.spam_score >= 0.8:
            spam_indicator = f"{Colors.RED}●{Colors.ENDC}"
        elif sender.spam_score >= 0.6:
            spam_indicator = f"{Colors.YELLOW}●{Colors.ENDC}"
        else:
            spam_indicator = f"{Colors.GREEN}●{Colors.ENDC}"
        
        type_tags = []
        if sender.is_newsletter:
            type_tags.append("Newsletter")
        if sender.is_automated:
            type_tags.append("Automated")
        
        table_data.append([
            i,
            sender.email[:40] + "..." if len(sender.email) > 40 else sender.email,
            sender.total_count,
            sender.unread_count,
            format_size(sender.total_size),
            f"{sender.spam_score:.0%}",
            spam_indicator,
            ", ".join(type_tags) or "-"
        ])
    
    headers = ["#", "Sender", "Emails", "Unread", "Size", "Spam", "", "Type"]
    print(tabulate(table_data, headers=headers, tablefmt="grid"))


def show_cleanup_suggestions(analyzer, senders):
    """Display cleanup suggestions"""
    print_header("Cleanup Suggestions")
    
    suggestions = analyzer.get_cleanup_suggestions(senders)
    
    if not suggestions:
        print(f"{Colors.GREEN}No cleanup suggestions - inbox looks clean!{Colors.ENDC}")
        return
    
    total_impact = {
        'emails': sum(s['impact']['email_count'] for s in suggestions),
        'size': sum(s['impact']['size_mb'] for s in suggestions)
    }
    
    print(f"\n{Colors.YELLOW}Potential cleanup impact:{Colors.ENDC}")
    print(f"  Emails: {total_impact['emails']:,}")
    print(f"  Size: {total_impact['size']:.1f} MB")
    
    print("\nTop suggestions:")
    for i, suggestion in enumerate(suggestions[:10], 1):
        confidence_color = Colors.RED if suggestion['confidence'] > 0.8 else Colors.YELLOW
        print(f"\n{i}. {Colors.BOLD}{suggestion['sender']}{Colors.ENDC}")
        print(f"   Reason: {suggestion['reason']}")
        print(f"   Impact: {suggestion['impact']['email_count']} emails, {suggestion['impact']['size_mb']:.1f} MB")
        print(f"   Confidence: {confidence_color}{suggestion['confidence']:.0%}{Colors.ENDC}")
        print(f"   Action: {suggestion['action']}")


def show_domain_analysis(emails):
    """Analyze emails by domain"""
    print_header("Domain Analysis")
    
    from collections import defaultdict
    domains = defaultdict(lambda: {'count': 0, 'size': 0, 'senders': set()})
    
    for email in emails:
        domain = email.sender_domain
        domains[domain]['count'] += 1
        domains[domain]['size'] += email.size
        domains[domain]['senders'].add(email.sender)
    
    # Sort by count
    sorted_domains = sorted(
        domains.items(),
        key=lambda x: x[1]['count'],
        reverse=True
    )[:15]
    
    table_data = []
    for domain, stats in sorted_domains:
        table_data.append([
            domain or "(no domain)",
            stats['count'],
            len(stats['senders']),
            format_size(stats['size'])
        ])
    
    headers = ["Domain", "Emails", "Unique Senders", "Total Size"]
    print(tabulate(table_data, headers=headers, tablefmt="grid"))


def export_results(senders, filename):
    """Export results to CSV"""
    print_header("Exporting Results")
    
    import csv
    
    with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = [
            'sender', 'domain', 'total_emails', 'unread_count',
            'total_size_mb', 'oldest_date', 'newest_date',
            'emails_per_day', 'spam_score', 'is_newsletter',
            'is_automated', 'has_unsubscribe', 'attachment_count'
        ]
        
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
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
                'has_unsubscribe': stats.has_unsubscribe,
                'attachment_count': stats.attachment_count
            })
    
    print(f"{Colors.GREEN}✓ Exported to {filename}{Colors.ENDC}")


def perform_dry_run(analyzer, senders, args):
    """Perform dry run deletion"""
    print_header("Dry Run - Deletion Preview")
    
    # Get suggestions
    suggestions = analyzer.get_cleanup_suggestions(senders)
    
    if not suggestions:
        print("No emails would be deleted")
        return
    
    # Filter by threshold
    to_delete = [s for s in suggestions if s['confidence'] >= args.threshold]
    
    if not to_delete:
        print(f"No senders meet the threshold ({args.threshold:.0%})")
        return
    
    print(f"\n{Colors.YELLOW}Would delete emails from {len(to_delete)} senders:{Colors.ENDC}")
    
    total_emails = 0
    total_size = 0
    
    for suggestion in to_delete:
        print(f"\n• {suggestion['sender']}")
        print(f"  Emails: {suggestion['impact']['email_count']}")
        print(f"  Size: {suggestion['impact']['size_mb']:.1f} MB")
        print(f"  Reason: {suggestion['reason']}")
        
        total_emails += suggestion['impact']['email_count']
        total_size += suggestion['impact']['size_mb']
    
    print(f"\n{Colors.BOLD}Total impact:{Colors.ENDC}")
    print(f"  Emails to delete: {total_emails:,}")
    print(f"  Space to recover: {total_size:.1f} MB")
    
    if args.confirm:
        response = input(f"\n{Colors.RED}Proceed with deletion? (yes/no): {Colors.ENDC}")
        if response.lower() == 'yes':
            print("\nDeleting emails...")
            for suggestion in to_delete:
                result = analyzer.delete_emails_by_sender(suggestion['sender'], dry_run=False)
                if result['success']:
                    print(f"{Colors.GREEN}✓{Colors.ENDC} {suggestion['sender']}: {result['message']}")
                else:
                    print(f"{Colors.RED}✗{Colors.ENDC} {suggestion['sender']}: {result['message']}")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Analyze Gmail inbox and identify cleanup opportunities',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Basic analysis
  %(prog)s --max-results 5000       # Analyze more emails
  %(prog)s --export                 # Export to CSV
  %(prog)s --dry-run                # Preview deletions
  %(prog)s --older-than 30          # Analyze emails older than 30 days
  %(prog)s --query "from:amazon"    # Analyze specific senders
        """
    )
    
    # Analysis options
    parser.add_argument('--max-results', type=int, default=1000,
                        help='Maximum emails to analyze (default: 1000)')
    parser.add_argument('--query', type=str,
                        help='Gmail search query')
    parser.add_argument('--unread-only', action='store_true',
                        help='Analyze only unread emails')
    parser.add_argument('--has-attachment', action='store_true',
                        help='Analyze only emails with attachments')
    parser.add_argument('--older-than', type=int,
                        help='Analyze emails older than N days')
    parser.add_argument('--newer-than', type=int,
                        help='Analyze emails newer than N days')
    
    # Output options
    parser.add_argument('--export', action='store_true',
                        help='Export results to CSV')
    parser.add_argument('--export-file', type=str, default='gmail_analysis.csv',
                        help='CSV export filename (default: gmail_analysis.csv)')
    parser.add_argument('--json', action='store_true',
                        help='Output results as JSON')
    parser.add_argument('--quiet', action='store_true',
                        help='Minimal output')
    
    # Action options
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be deleted')
    parser.add_argument('--threshold', type=float, default=0.7,
                        help='Spam score threshold for deletion (0.0-1.0, default: 0.7)')
    parser.add_argument('--confirm', action='store_true',
                        help='Confirm and execute deletions after dry run')
    
    # Display options
    parser.add_argument('--top-senders', type=int, default=20,
                        help='Number of top senders to show (default: 20)')
    parser.add_argument('--skip-suggestions', action='store_true',
                        help='Skip cleanup suggestions')
    parser.add_argument('--skip-domains', action='store_true',
                        help='Skip domain analysis')
    
    args = parser.parse_args()
    
    # Initialize analyzer
    print(f"{Colors.CYAN}Gmail AI Cleaner - Command Line Analyzer{Colors.ENDC}")
    print("=====================================")
    
    analyzer = GmailAnalyzer()
    
    # Authenticate
    print("\nAuthenticating with Gmail API...")
    try:
        if not analyzer.authenticate():
            print(f"{Colors.RED}Authentication failed!{Colors.ENDC}")
            return 1
        print(f"{Colors.GREEN}✓ Authenticated successfully{Colors.ENDC}")
    except Exception as e:
        print(f"{Colors.RED}Authentication error: {e}{Colors.ENDC}")
        return 1
    
    # Perform analysis
    emails, senders = analyze_inbox(analyzer, args)
    
    if not emails:
        return 0
    
    # Show results based on options
    if not args.quiet and not args.json:
        show_top_senders(senders, args.top_senders)
        
        if not args.skip_domains:
            show_domain_analysis(emails)
        
        if not args.skip_suggestions:
            show_cleanup_suggestions(analyzer, senders)
    
    # Export if requested
    if args.export:
        export_results(senders, args.export_file)
    
    # JSON output
    if args.json:
        output = {
            'summary': {
                'total_emails': len(emails),
                'unique_senders': len(senders),
                'total_size_bytes': sum(e.size for e in emails),
                'analysis_date': datetime.now().isoformat()
            },
            'senders': [
                {
                    'email': s.email,
                    'count': s.total_count,
                    'size': s.total_size,
                    'spam_score': s.spam_score
                }
                for s in sorted(senders.values(), key=lambda x: x.total_count, reverse=True)[:50]
            ]
        }
        print(json.dumps(output, indent=2))
    
    # Dry run if requested
    if args.dry_run:
        perform_dry_run(analyzer, senders, args)
    
    print(f"\n{Colors.GREEN}Analysis complete!{Colors.ENDC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
