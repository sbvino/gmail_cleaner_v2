#!/bin/bash

# Gmail Cleaner Backup Script
# Creates timestamped backups of database and configuration

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKUP_ROOT="${PROJECT_ROOT}/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="${BACKUP_ROOT}/${TIMESTAMP}"
MAX_BACKUPS=7  # Keep only last 7 backups

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Create backup directory
mkdir -p "$BACKUP_DIR"

log_info "Starting backup to ${BACKUP_DIR}"

# Backup database
if [ -f "${PROJECT_ROOT}/data/gmail_cleaner.db" ]; then
    log_info "Backing up database..."
    cp "${PROJECT_ROOT}/data/gmail_cleaner.db" "${BACKUP_DIR}/"
    
    # Also create a SQL dump for extra safety
    if command -v sqlite3 &> /dev/null; then
        sqlite3 "${PROJECT_ROOT}/data/gmail_cleaner.db" ".dump" > "${BACKUP_DIR}/gmail_cleaner.sql"
        log_info "Created SQL dump"
    fi
else
    log_warn "Database not found, skipping..."
fi

# Backup configuration
if [ -d "${PROJECT_ROOT}/config" ]; then
    log_info "Backing up configuration..."
    cp -r "${PROJECT_ROOT}/config" "${BACKUP_DIR}/"
else
    log_warn "Config directory not found, skipping..."
fi

# Backup OAuth token (if exists)
if [ -f "${PROJECT_ROOT}/token.pickle" ]; then
    log_info "Backing up OAuth token..."
    cp "${PROJECT_ROOT}/token.pickle" "${BACKUP_DIR}/"
fi

# Backup environment file (without secrets)
if [ -f "${PROJECT_ROOT}/.env" ]; then
    log_info "Backing up environment template..."
    # Remove sensitive values
    sed 's/SECRET_KEY=.*/SECRET_KEY=<REDACTED>/' "${PROJECT_ROOT}/.env" > "${BACKUP_DIR}/.env.template"
fi

# Create backup metadata
cat > "${BACKUP_DIR}/backup_info.txt" << EOF
Backup Information
==================
Timestamp: ${TIMESTAMP}
Date: $(date)
System: $(uname -a)
Docker Status: $(docker --version 2>/dev/null || echo "Not installed")

Files Backed Up:
$(ls -la "${BACKUP_DIR}")

Database Info:
$(if [ -f "${BACKUP_DIR}/gmail_cleaner.db" ]; then
    echo "Size: $(du -h "${BACKUP_DIR}/gmail_cleaner.db" | cut -f1)"
    echo "Tables: $(sqlite3 "${BACKUP_DIR}/gmail_cleaner.db" ".tables" 2>/dev/null || echo "Could not read")"
else
    echo "No database found"
fi)
EOF

# Compress backup
log_info "Compressing backup..."
cd "${BACKUP_ROOT}"
tar -czf "${TIMESTAMP}.tar.gz" "${TIMESTAMP}"
rm -rf "${TIMESTAMP}"

# Calculate backup size
BACKUP_SIZE=$(du -h "${BACKUP_ROOT}/${TIMESTAMP}.tar.gz" | cut -f1)
log_info "Backup created: ${TIMESTAMP}.tar.gz (${BACKUP_SIZE})"

# Cleanup old backups
log_info "Cleaning up old backups..."
cd "${BACKUP_ROOT}"
ls -t *.tar.gz 2>/dev/null | tail -n +$((MAX_BACKUPS + 1)) | while read old_backup; do
    log_warn "Removing old backup: ${old_backup}"
    rm -f "${old_backup}"
done

# Verify backup
if tar -tzf "${BACKUP_ROOT}/${TIMESTAMP}.tar.gz" > /dev/null 2>&1; then
    log_info "Backup verified successfully"
else
    log_error "Backup verification failed!"
    exit 1
fi

# Optional: Upload to cloud storage
if [ -n "$BACKUP_S3_BUCKET" ]; then
    if command -v aws &> /dev/null; then
        log_info "Uploading to S3..."
        aws s3 cp "${BACKUP_ROOT}/${TIMESTAMP}.tar.gz" "s3://${BACKUP_S3_BUCKET}/gmail-cleaner-backups/"
    else
        log_warn "AWS CLI not found, skipping S3 upload"
    fi
fi

log_info "Backup completed successfully!"

# Show restore instructions
echo ""
echo "To restore from this backup:"
echo "1. Stop the application: docker-compose down"
echo "2. Extract backup: tar -xzf backups/${TIMESTAMP}.tar.gz -C backups/"
echo "3. Restore database: cp backups/${TIMESTAMP}/gmail_cleaner.db data/"
echo "4. Restore config: cp -r backups/${TIMESTAMP}/config/* config/"
echo "5. Restart application: docker-compose up -d"
