#!/bin/bash

# Gmail AI Cleaner Setup Script
# This script sets up the application environment and dependencies

set -e

echo "Gmail AI Cleaner Setup Script"
echo "============================"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[✓]${NC} $1"
}

print_error() {
    echo -e "${RED}[✗]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

# Check if running as root
if [[ $EUID -eq 0 ]]; then
   print_error "This script should not be run as root!"
   exit 1
fi

# Create directory structure
print_status "Creating directory structure..."
mkdir -p data config logs ssl static/css static/js templates scripts monitoring

# Check for Docker
if ! command -v docker &> /dev/null; then
    print_error "Docker is not installed. Please install Docker first."
    exit 1
fi

# Check for Docker Compose
if ! command -v docker-compose &> /dev/null; then
    print_error "Docker Compose is not installed. Please install Docker Compose first."
    exit 1
fi

# Generate SSL certificates (self-signed for development)
if [ ! -f ssl/cert.pem ]; then
    print_status "Generating self-signed SSL certificates..."
    openssl req -x509 -newkey rsa:4096 -keyout ssl/key.pem -out ssl/cert.pem \
        -days 365 -nodes -subj "/C=US/ST=State/L=City/O=Organization/CN=localhost"
fi

# Check for credentials.json
if [ ! -f credentials.json ]; then
    print_warning "credentials.json not found!"
    echo "Please follow these steps to create it:"
    echo "1. Go to https://console.cloud.google.com/"
    echo "2. Create a new project or select existing"
    echo "3. Enable Gmail API"
    echo "4. Create credentials (OAuth 2.0 Client ID)"
    echo "5. Download and save as credentials.json in the root directory"
    exit 1
fi

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    print_status "Creating .env file..."
    cat > .env << EOF
# Flask Configuration
SECRET_KEY=$(openssl rand -hex 32)
FLASK_ENV=production

# Redis Configuration
REDIS_HOST=redis
REDIS_PORT=6379

# Database Configuration
DB_PATH=/app/data/gmail_cleaner.db

# OAuth Configuration
OAUTHLIB_INSECURE_TRANSPORT=0
OAUTHLIB_RELAX_TOKEN_SCOPE=1

# Timezone
TZ=UTC

# Monitoring (optional)
ENABLE_MONITORING=false
EOF
    print_status ".env file created with default values"
fi

# Create default patterns.yaml if it doesn't exist
if [ ! -f config/patterns.yaml ]; then
    print_status "Creating default patterns.yaml..."
    # The patterns.yaml content is already created in previous artifact
    print_warning "Please copy the patterns.yaml content to config/patterns.yaml"
fi

# Set permissions
print_status "Setting file permissions..."
chmod 600 credentials.json
chmod 600 .env
chmod 700 scripts/*.sh

# Create monitoring configuration
print_status "Creating monitoring configuration..."
cat > monitoring/prometheus.yml << EOF
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'gmail-cleaner'
    static_configs:
      - targets: ['app:5000']
    
  - job_name: 'nginx'
    static_configs:
      - targets: ['nginx:8080']
    
  - job_name: 'redis'
    static_configs:
      - targets: ['redis:6379']
EOF

# Create Grafana datasource configuration
cat > monitoring/grafana-datasources.yml << EOF
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
EOF

# Create backup script
cat > scripts/backup.sh << 'EOF'
#!/bin/bash
# Backup script for Gmail Cleaner

BACKUP_DIR="backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

# Backup database
cp data/gmail_cleaner.db "$BACKUP_DIR/"

# Backup configuration
cp -r config "$BACKUP_DIR/"

# Backup token
cp token.pickle "$BACKUP_DIR/" 2>/dev/null || true

# Compress backup
tar -czf "$BACKUP_DIR.tar.gz" "$BACKUP_DIR"
rm -rf "$BACKUP_DIR"

echo "Backup created: $BACKUP_DIR.tar.gz"

# Keep only last 7 backups
find backups -name "*.tar.gz" -mtime +7 -delete
EOF

chmod +x scripts/backup.sh

# Create analyze script
cat > scripts/analyze.py << 'EOF'
#!/usr/bin/env python3
"""
Standalone analyzer script for Gmail cleanup analysis
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer import GmailAnalyzer
import argparse

def main():
    parser = argparse.ArgumentParser(description='Analyze Gmail inbox')
    parser.add_argument('--max-results', type=int, default=1000, help='Maximum emails to analyze')
    parser.add_argument('--export', action='store_true', help='Export results to CSV')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be deleted')
    args = parser.parse_args()
    
    analyzer = GmailAnalyzer()
    
    print("Authenticating...")
    if not analyzer.authenticate():
        print("Authentication failed!")
        return
    
    print(f"Fetching up to {args.max_results} emails...")
    emails = analyzer.fetch_emails_batch(max_results=args.max_results)
    
    print(f"Analyzing {len(emails)} emails...")
    senders = analyzer.analyze_senders(emails)
    
    print("\nTop 10 senders by volume:")
    sorted_senders = sorted(senders.values(), key=lambda s: s.total_count, reverse=True)[:10]
    
    for i, sender in enumerate(sorted_senders, 1):
        print(f"{i}. {sender.email}")
        print(f"   Emails: {sender.total_count}, Unread: {sender.unread_count}")
        print(f"   Size: {sender.total_size / (1024*1024):.1f} MB")
        print(f"   Spam Score: {sender.spam_score:.0%}")
        print()
    
    if args.export:
        csv_data = analyzer.export_statistics(senders)
        with open('gmail_analysis.csv', 'wb') as f:
            f.write(csv_data)
        print("Results exported to gmail_analysis.csv")
    
    if args.dry_run:
        suggestions = analyzer.get_cleanup_suggestions(senders)
        total_emails = sum(s['impact']['email_count'] for s in suggestions)
        total_size = sum(s['impact']['size_mb'] for s in suggestions)
        print(f"\nCleanup suggestions would delete {total_emails} emails ({total_size:.1f} MB)")

if __name__ == "__main__":
    main()
EOF

chmod +x scripts/analyze.py

# Build Docker images
print_status "Building Docker images..."
docker-compose build

# Initialize database
print_status "Initializing database..."
docker-compose run --rm app python -c "from analyzer import GmailAnalyzer; GmailAnalyzer()"

print_status "Setup complete!"
echo ""
echo "Next steps:"
echo "1. Make sure credentials.json is in place"
echo "2. Run: docker-compose up -d"
echo "3. Visit: https://localhost"
echo "4. First run will require OAuth authentication"
echo ""
print_warning "Note: Using self-signed SSL certificate. Your browser will show a warning."
