"""
Background scheduler for automated cleanup rules
Runs as a separate container for reliability
"""

import os
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import redis

from analyzer import GmailAnalyzer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class CleanupScheduler:
    """Manages scheduled cleanup rules"""
    
    def __init__(self):
        self.scheduler = BlockingScheduler()
        self.analyzer = GmailAnalyzer()
        self.db_path = os.getenv('DB_PATH', 'data/gmail_cleaner.db')
        self.redis_client = self._init_redis()
        
        # Authenticate Gmail API
        try:
            self.analyzer.authenticate()
            logger.info("Gmail API authenticated for scheduler")
        except Exception as e:
            logger.error(f"Failed to authenticate: {e}")
            raise
    
    def _init_redis(self):
        """Initialize Redis connection"""
        try:
            client = redis.Redis(
                host=os.getenv('REDIS_HOST', 'localhost'),
                port=int(os.getenv('REDIS_PORT', 6379)),
                decode_responses=True
            )
            client.ping()
            return client
        except Exception as e:
            logger.warning(f"Redis not available: {e}")
            return None
    
    def load_rules(self):
        """Load active cleanup rules from database"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM cleanup_rules WHERE is_active = 1"
            )
            rules = cursor.fetchall()
            conn.close()
            
            for rule in rules:
                self._schedule_rule(rule)
            
            logger.info(f"Loaded {len(rules)} active rules")
            
        except Exception as e:
            logger.error(f"Error loading rules: {e}")
    
    def _schedule_rule(self, rule):
        """Schedule a single rule"""
        try:
            rule_id = rule['id']
            criteria = json.loads(rule['criteria'])
            
            # Create job function
            def run_cleanup():
                logger.info(f"Running cleanup rule: {rule['name']}")
                try:
                    # Run cleanup
                    result = self.analyzer.delete_by_criteria(criteria, dry_run=False)
                    
                    # Log results
                    logger.info(f"Rule '{rule['name']}' completed: {result}")
                    
                    # Update last run time
                    self._update_last_run(rule_id)
                    
                    # Publish to Redis for UI updates
                    if self.redis_client:
                        self.redis_client.publish('cleanup_completed', json.dumps({
                            'rule_id': rule_id,
                            'rule_name': rule['name'],
                            'result': result,
                            'timestamp': datetime.now().isoformat()
                        }))
                        
                except Exception as e:
                    logger.error(f"Error running rule '{rule['name']}': {e}")
            
            # Parse schedule configuration
            schedule_config = json.loads(rule.get('schedule', '{}'))
            
            if schedule_config.get('type') == 'cron':
                # Cron-based schedule
                trigger = CronTrigger(
                    hour=schedule_config.get('hour', 0),
                    minute=schedule_config.get('minute', 0),
                    day_of_week=schedule_config.get('day_of_week', '*'),
                    day=schedule_config.get('day', '*'),
                    month=schedule_config.get('month', '*')
                )
            elif schedule_config.get('type') == 'interval':
                # Interval-based schedule
                interval_type = schedule_config.get('interval_type', 'days')
                interval_value = schedule_config.get('interval_value', 1)
                
                kwargs = {interval_type: interval_value}
                trigger = IntervalTrigger(**kwargs)
            else:
                # Default: daily at midnight
                trigger = CronTrigger(hour=0, minute=0)
            
            # Add job to scheduler
            self.scheduler.add_job(
                run_cleanup,
                trigger=trigger,
                id=f"rule_{rule_id}",
                replace_existing=True,
                max_instances=1,
                coalesce=True
            )
            
            logger.info(f"Scheduled rule: {rule['name']} (ID: {rule_id})")
            
        except Exception as e:
            logger.error(f"Error scheduling rule {rule.get('name', 'unknown')}: {e}")
    
    def _update_last_run(self, rule_id):
        """Update last run time for a rule"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "UPDATE cleanup_rules SET last_run = ? WHERE id = ?",
                (datetime.now(), rule_id)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error updating last run time: {e}")
    
    def run_daily_maintenance(self):
        """Daily maintenance tasks"""
        logger.info("Running daily maintenance...")
        
        try:
            # Clean up old deleted emails (> 30 days)
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                DELETE FROM email_history 
                WHERE deleted_at < datetime('now', '-30 days')
            """)
            deleted = conn.total_changes
            conn.commit()
            conn.close()
            
            logger.info(f"Cleaned up {deleted} old email records")
            
            # Clear old cache entries
            if self.redis_client:
                self.redis_client.flushdb()
                logger.info("Cleared Redis cache")
            
            # Vacuum database
            conn = sqlite3.connect(self.db_path)
            conn.execute("VACUUM")
            conn.close()
            logger.info("Database vacuumed")
            
        except Exception as e:
            logger.error(f"Error in daily maintenance: {e}")
    
    def run_hourly_stats(self):
        """Update hourly statistics"""
        logger.info("Updating hourly statistics...")
        
        try:
            # Fetch recent emails
            emails = self.analyzer.fetch_emails_batch(
                query=f"after:{(datetime.now() - timedelta(hours=1)).strftime('%Y/%m/%d')}",
                max_results=100
            )
            
            if emails:
                # Update sender stats
                senders = self.analyzer.analyze_senders(emails)
                logger.info(f"Updated stats for {len(senders)} senders")
            
        except Exception as e:
            logger.error(f"Error updating stats: {e}")
    
    def start(self):
        """Start the scheduler"""
        # Load cleanup rules
        self.load_rules()
        
        # Schedule maintenance tasks
        self.scheduler.add_job(
            self.run_daily_maintenance,
            trigger=CronTrigger(hour=3, minute=0),  # 3 AM daily
            id="daily_maintenance",
            replace_existing=True
        )
        
        # Schedule hourly stats update
        self.scheduler.add_job(
            self.run_hourly_stats,
            trigger=IntervalTrigger(hours=1),
            id="hourly_stats",
            replace_existing=True
        )
        
        # Reload rules every hour in case they changed
        self.scheduler.add_job(
            self.load_rules,
            trigger=IntervalTrigger(hours=1),
            id="reload_rules",
            replace_existing=True
        )
        
        logger.info("Scheduler started")
        
        try:
            self.scheduler.start()
        except KeyboardInterrupt:
            logger.info("Scheduler stopped by user")
            self.scheduler.shutdown()
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            self.scheduler.shutdown()


def main():
    """Main entry point"""
    scheduler = CleanupScheduler()
    scheduler.start()


if __name__ == "__main__":
    main()
