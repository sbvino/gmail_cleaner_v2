#!/bin/bash

# Fix permissions for Gmail Cleaner
# Run this if you encounter database or permission errors

echo "Fixing permissions for Gmail Cleaner..."

# Create directories if they don't exist
mkdir -p data logs config ssl backups

# Set permissions
echo "Setting directory permissions..."
chmod 777 data logs
chmod 755 config ssl backups scripts

# Fix ownership if running as root
if [ "$EUID" -eq 0 ]; then
    echo "Running as root, setting ownership..."
    chown -R 1000:1000 data logs config
fi

# Create empty database if it doesn't exist
if [ ! -f "data/gmail_cleaner.db" ]; then
    echo "Creating empty database..."
    touch data/gmail_cleaner.db
    chmod 666 data/gmail_cleaner.db
fi

# Set script permissions
chmod +x scripts/*.sh 2>/dev/null || true
chmod +x scripts/*.py 2>/dev/null || true

# Initialize database if needed
if command -v python3 &> /dev/null; then
    echo "Initializing database..."
    python3 scripts/init-db.py 2>/dev/null || true
fi

echo "Permissions fixed!"
echo ""
echo "If you're still having issues:"
echo "1. Stop containers: docker-compose down"
echo "2. Remove volumes: docker-compose down -v"
echo "3. Run this script again"
echo "4. Start containers: docker-compose up -d"
