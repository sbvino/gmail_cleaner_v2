#!/usr/bin/env python3
"""
Initialize Gmail Cleaner Database
Run this script to create the database and tables
"""

import os
import sys
import sqlite3
import logging

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_database():
    """Initialize the database with all required tables"""
    db_path = os.getenv('DB_PATH', 'data/gmail_cleaner.db')
    
    # Ensure directory exists
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        logger.info(f"Created directory: {db_dir}")
    
    # Connect to database
    logger.info(f"Initializing database: {db_path}")
    conn = sqlite3.connect(db_path)
    
    try:
        # Create tables
        conn.executescript("""
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
                created_at TIMESTAMP,
                last_run TIMESTAMP,
                schedule TEXT
            );
            
            CREATE INDEX IF NOT EXISTS idx_sender ON email_history(sender);
            CREATE INDEX IF NOT EXISTS idx_deleted_at ON email_history(deleted_at);
        """)
        
        conn.commit()
        logger.info("Database tables created successfully")
        
        # Verify tables
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        logger.info(f"Created tables: {[t[0] for t in tables]}")
        
        # Set permissions
        if os.path.exists(db_path):
            os.chmod(db_path, 0o666)
            logger.info("Set database permissions")
        
    except Exception as e:
        logger.error(f"Error creating tables: {e}")
        raise
    finally:
        conn.close()
    
    logger.info("Database initialization complete")

if __name__ == "__main__":
    try:
        init_database()
        print("✓ Database initialized successfully")
    except Exception as e:
        print(f"✗ Failed to initialize database: {e}")
        sys.exit(1)
