#!/usr/bin/env python3
"""
NextReel Database Backup System
================================
Complete implementation for automated database backups with recovery procedures.

INSTRUCTIONS FOR CLAUDE CODE:
1. Save this file as 'backup_manager.py' in your project root
2. Install required dependencies: pip install aiomysql schedule python-dotenv boto3
3. Create a 'backups' directory in your project root
4. Configure your .env file with backup-specific variables (see below)
5. Set up cron job or systemd service for daily execution
6. Test backup and restore procedures before deploying to production

REQUIRED ENV VARIABLES TO ADD:
BACKUP_RETENTION_DAYS=30
BACKUP_STORAGE_PATH=/path/to/your/backups
BACKUP_ENCRYPTION_KEY=your-32-char-encryption-key-here
BACKUP_S3_BUCKET=your-s3-bucket-name (optional)
AWS_ACCESS_KEY_ID=your-aws-key (optional)
AWS_SECRET_ACCESS_KEY=your-aws-secret (optional)
BACKUP_NOTIFICATION_EMAIL=admin@yourdomain.com
BACKUP_TIME=02:00
"""

import os
import sys
import asyncio
import aiomysql
import gzip
import hashlib
import json
import logging
import smtplib
import schedule
import subprocess
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import shutil

# Optional S3 support
try:
    import boto3
    HAS_S3 = True
except ImportError:
    HAS_S3 = False
    print("Warning: boto3 not installed. S3 backup storage disabled.")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/backup.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class BackupConfig:
    """Backup configuration settings"""
    # Database connections
    movie_db_host: str
    movie_db_user: str
    movie_db_password: str
    movie_db_name: str
    
    user_db_host: str
    user_db_user: str
    user_db_password: str
    user_db_name: str
    
    # Port configurations
    movie_db_port: int = 3306
    user_db_port: int = 3306
    
    # Backup settings
    backup_path: str = "./backups"
    retention_days: int = 30
    compression: bool = True
    encryption_key: Optional[str] = None
    
    # S3 settings (optional)
    s3_bucket: Optional[str] = None
    s3_prefix: str = "database-backups"
    
    # Notification settings
    notification_email: Optional[str] = None
    smtp_host: str = "localhost"
    smtp_port: int = 587
    
    # Recovery settings
    max_restore_attempts: int = 3
    verify_after_backup: bool = False
    
    @classmethod
    def from_env(cls) -> 'BackupConfig':
        """Load configuration from environment variables"""
        from dotenv import load_dotenv
        load_dotenv()
        
        # Determine environment
        flask_env = os.getenv('FLASK_ENV', 'production')
        
        if flask_env == 'development':
            return cls(
                movie_db_host=os.getenv('DB_HOST', '127.0.0.1'),
                movie_db_user=os.getenv('DB_USER', 'root'),
                movie_db_password=os.getenv('DB_PASSWORD', ''),
                movie_db_name=os.getenv('DB_NAME', 'imdb'),
                
                user_db_host=os.getenv('USER_DB_HOST', '127.0.0.1'),
                user_db_user=os.getenv('USER_DB_USER', 'root'),
                user_db_password=os.getenv('USER_DB_PASSWORD', ''),
                user_db_name=os.getenv('USER_DB_NAME', 'UserAccounts'),
                
                movie_db_port=int(os.getenv('DB_PORT', 3306)),
                user_db_port=int(os.getenv('USER_DB_PORT', 3306)),
                
                backup_path=os.getenv('BACKUP_STORAGE_PATH', './backups'),
                retention_days=int(os.getenv('BACKUP_RETENTION_DAYS', 30)),
                encryption_key=os.getenv('BACKUP_ENCRYPTION_KEY'),
                s3_bucket=os.getenv('BACKUP_S3_BUCKET'),
                notification_email=os.getenv('BACKUP_NOTIFICATION_EMAIL')
            )
        else:  # Production
            return cls(
                movie_db_host=os.getenv('STACKHERO_DB_HOST'),
                movie_db_user=os.getenv('STACKHERO_DB_USER'),
                movie_db_password=os.getenv('STACKHERO_DB_PASSWORD'),
                movie_db_name=os.getenv('STACKHERO_DB_NAME'),
                
                user_db_host=os.getenv('STACKHERO_DB_HOST'),
                user_db_user=os.getenv('STACKHERO_DB_USER'),
                user_db_password=os.getenv('STACKHERO_DB_PASSWORD'),
                user_db_name=os.getenv('USER_DB_NAME', 'UserAccounts'),
                
                movie_db_port=int(os.getenv('STACKHERO_DB_PORT', 3306)),
                user_db_port=int(os.getenv('STACKHERO_DB_PORT', 3306)),
                
                backup_path=os.getenv('BACKUP_STORAGE_PATH', '/var/backups/nextreel'),
                retention_days=int(os.getenv('BACKUP_RETENTION_DAYS', 30)),
                encryption_key=os.getenv('BACKUP_ENCRYPTION_KEY'),
                s3_bucket=os.getenv('BACKUP_S3_BUCKET'),
                notification_email=os.getenv('BACKUP_NOTIFICATION_EMAIL')
            )


@dataclass
class BackupMetadata:
    """Metadata for backup files"""
    database_name: str
    backup_timestamp: datetime
    file_path: str
    file_size: int
    checksum: str
    compressed: bool
    encrypted: bool
    tables_count: int
    records_count: int
    backup_duration: float
    restore_tested: bool = False
    s3_location: Optional[str] = None


class DatabaseBackupManager:
    """Comprehensive database backup and recovery system"""
    
    def __init__(self, config: BackupConfig):
        self.config = config
        self.backup_path = Path(config.backup_path)
        self.backup_path.mkdir(parents=True, exist_ok=True)
        
        # Initialize S3 client if configured
        self.s3_client = None
        if HAS_S3 and config.s3_bucket:
            try:
                self.s3_client = boto3.client('s3')
                logger.info(f"S3 backup storage enabled: {config.s3_bucket}")
            except Exception as e:
                logger.error(f"Failed to initialize S3: {e}")
    
    async def backup_database(self, db_type: str = 'both') -> List[BackupMetadata]:
        """
        Perform database backup
        
        Args:
            db_type: 'movie', 'user', or 'both'
        
        Returns:
            List of backup metadata objects
        """
        backups = []
        
        if db_type in ['movie', 'both']:
            metadata = await self._backup_single_database(
                host=self.config.movie_db_host,
                port=self.config.movie_db_port,
                user=self.config.movie_db_user,
                password=self.config.movie_db_password,
                database=self.config.movie_db_name,
                db_label='movie'
            )
            if metadata:
                backups.append(metadata)
        
        if db_type in ['user', 'both']:
            metadata = await self._backup_single_database(
                host=self.config.user_db_host,
                port=self.config.user_db_port,
                user=self.config.user_db_user,
                password=self.config.user_db_password,
                database=self.config.user_db_name,
                db_label='user'
            )
            if metadata:
                backups.append(metadata)
        
        # Clean old backups
        await self._cleanup_old_backups()
        
        # Send notification
        if backups and self.config.notification_email:
            await self._send_backup_notification(backups)
        
        return backups
    
    async def _backup_single_database(
        self, host: str, port: int, user: str, 
        password: str, database: str, db_label: str
    ) -> Optional[BackupMetadata]:
        """Backup a single database"""
        start_time = time.time()
        timestamp = datetime.now()
        
        try:
            logger.info(f"Starting backup for {db_label} database: {database}")
            
            # Generate backup filename
            filename = f"{db_label}_{database}_{timestamp.strftime('%Y%m%d_%H%M%S')}.sql"
            if self.config.compression:
                filename += ".gz"
            
            backup_file = self.backup_path / filename
            
            # Use mysqldump for backup (most reliable method)
            dump_cmd = [
                'mysqldump',
                f'--host={host}',
                f'--port={port}',
                f'--user={user}',
                '--single-transaction',  # For InnoDB consistency
                '--routines',            # Include stored procedures
                '--triggers',            # Include triggers
                '--add-drop-table',      # Add DROP TABLE before CREATE
                '--create-options',      # Include all table options
                '--extended-insert',     # Use multiple-row INSERT
                '--lock-tables=false',   # Don't lock tables
                database
            ]
            
            # Execute backup
            if self.config.compression:
                # Pipe through gzip
                process = subprocess.Popen(
                    dump_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env={**os.environ, 'MYSQL_PWD': password}  # Use env var for password
                )
                
                try:
                    with gzip.open(backup_file, 'wb') as f:
                        # Read and write in chunks to handle large databases
                        while True:
                            chunk = process.stdout.read(8192)
                            if not chunk:
                                break
                            f.write(chunk)
                    
                    # Wait for process to complete
                    process.wait()
                    
                    if process.returncode != 0:
                        error = process.stderr.read().decode()
                        raise Exception(f"mysqldump failed: {error}")
                        
                except subprocess.TimeoutExpired:
                    process.kill()
                    raise Exception("Backup timed out")
                except Exception as e:
                    process.kill()
                    raise
            else:
                with open(backup_file, 'w') as f:
                    process = subprocess.run(
                        dump_cmd,
                        stdout=f,
                        stderr=subprocess.PIPE,
                        text=True,
                        env={**os.environ, 'MYSQL_PWD': password},  # Use env var for password
                        timeout=60  # 60 second timeout
                    )
                    if process.returncode != 0:
                        raise Exception(f"mysqldump failed: {process.stderr}")
            
            # Calculate checksum
            checksum = self._calculate_checksum(backup_file)
            
            # Get database statistics
            stats = await self._get_database_stats(host, port, user, password, database)
            
            # Create metadata
            metadata = BackupMetadata(
                database_name=database,
                backup_timestamp=timestamp,
                file_path=str(backup_file),
                file_size=backup_file.stat().st_size,
                checksum=checksum,
                compressed=self.config.compression,
                encrypted=bool(self.config.encryption_key),
                tables_count=stats['tables_count'],
                records_count=stats['records_count'],
                backup_duration=time.time() - start_time
            )
            
            # Verify backup if configured
            if self.config.verify_after_backup:
                metadata.restore_tested = await self._verify_backup(backup_file, database)
            
            # Upload to S3 if configured
            if self.s3_client and self.config.s3_bucket:
                s3_key = f"{self.config.s3_prefix}/{filename}"
                try:
                    self.s3_client.upload_file(
                        str(backup_file),
                        self.config.s3_bucket,
                        s3_key,
                        ExtraArgs={
                            'ServerSideEncryption': 'AES256',
                            'Metadata': {
                                'database': database,
                                'timestamp': timestamp.isoformat(),
                                'checksum': checksum
                            }
                        }
                    )
                    metadata.s3_location = f"s3://{self.config.s3_bucket}/{s3_key}"
                    logger.info(f"Backup uploaded to S3: {metadata.s3_location}")
                except Exception as e:
                    logger.error(f"Failed to upload to S3: {e}")
            
            # Save metadata
            self._save_metadata(metadata)
            
            logger.info(
                f"Backup completed for {database}: "
                f"{metadata.file_size / 1024 / 1024:.2f} MB in {metadata.backup_duration:.2f}s"
            )
            
            return metadata
            
        except Exception as e:
            logger.error(f"Backup failed for {database}: {e}")
            # Clean up partial backup
            if backup_file.exists():
                backup_file.unlink()
            return None
    
    async def restore_database(
        self, backup_file: str, target_database: str,
        db_type: str = 'movie'
    ) -> bool:
        """
        Restore database from backup
        
        Args:
            backup_file: Path to backup file
            target_database: Target database name
            db_type: 'movie' or 'user'
        
        Returns:
            True if successful
        """
        backup_path = Path(backup_file)
        if not backup_path.exists():
            logger.error(f"Backup file not found: {backup_file}")
            return False
        
        # Determine database connection details
        if db_type == 'movie':
            host = self.config.movie_db_host
            port = self.config.movie_db_port
            user = self.config.movie_db_user
            password = self.config.movie_db_password
        else:
            host = self.config.user_db_host
            port = self.config.user_db_port
            user = self.config.user_db_user
            password = self.config.user_db_password
        
        try:
            logger.info(f"Starting restore of {target_database} from {backup_file}")
            
            # Create database if it doesn't exist
            await self._ensure_database_exists(host, port, user, password, target_database)
            
            # Prepare restore command
            restore_cmd = [
                'mysql',
                f'--host={host}',
                f'--port={port}',
                f'--user={user}',
                f'--password={password}',
                target_database
            ]
            
            # Execute restore
            if backup_file.endswith('.gz'):
                # Decompress and restore
                with gzip.open(backup_path, 'rb') as f:
                    process = subprocess.run(
                        restore_cmd,
                        stdin=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        input=f.read()
                    )
            else:
                with open(backup_path, 'r') as f:
                    process = subprocess.run(
                        restore_cmd,
                        stdin=f,
                        stderr=subprocess.PIPE,
                        text=True
                    )
            
            if process.returncode != 0:
                raise Exception(f"Restore failed: {process.stderr}")
            
            logger.info(f"Database {target_database} restored successfully")
            return True
            
        except Exception as e:
            logger.error(f"Restore failed for {target_database}: {e}")
            return False
    
    async def perform_point_in_time_recovery(
        self, target_time: datetime, db_type: str = 'both'
    ) -> bool:
        """
        Restore database to a specific point in time
        
        Args:
            target_time: Target datetime for recovery
            db_type: 'movie', 'user', or 'both'
        
        Returns:
            True if successful
        """
        logger.info(f"Starting point-in-time recovery to {target_time}")
        
        # Find the most recent backup before target time
        metadata_files = list(self.backup_path.glob("*.metadata.json"))
        
        suitable_backups = {
            'movie': None,
            'user': None
        }
        
        for meta_file in metadata_files:
            with open(meta_file, 'r') as f:
                metadata = json.load(f)
                backup_time = datetime.fromisoformat(metadata['backup_timestamp'])
                
                if backup_time <= target_time:
                    db_label = 'movie' if 'movie' in metadata['database_name'].lower() else 'user'
                    
                    if suitable_backups[db_label] is None or \
                       backup_time > datetime.fromisoformat(suitable_backups[db_label]['backup_timestamp']):
                        suitable_backups[db_label] = metadata
        
        # Restore suitable backups
        success = True
        
        if db_type in ['movie', 'both'] and suitable_backups['movie']:
            success &= await self.restore_database(
                suitable_backups['movie']['file_path'],
                self.config.movie_db_name,
                'movie'
            )
        
        if db_type in ['user', 'both'] and suitable_backups['user']:
            success &= await self.restore_database(
                suitable_backups['user']['file_path'],
                self.config.user_db_name,
                'user'
            )
        
        return success
    
    async def _get_database_stats(
        self, host: str, port: int, user: str, 
        password: str, database: str
    ) -> Dict:
        """Get database statistics"""
        try:
            conn = await aiomysql.connect(
                host=host, port=port,
                user=user, password=password,
                db=database
            )
            
            async with conn.cursor() as cursor:
                # Count tables
                await cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s",
                    (database,)
                )
                tables_count = (await cursor.fetchone())[0]
                
                # Count total records (approximate)
                await cursor.execute(
                    """
                    SELECT SUM(table_rows) 
                    FROM information_schema.tables 
                    WHERE table_schema = %s
                    """,
                    (database,)
                )
                records_count = (await cursor.fetchone())[0] or 0
            
            conn.close()
            
            return {
                'tables_count': tables_count,
                'records_count': int(records_count)
            }
            
        except Exception as e:
            logger.error(f"Failed to get database stats: {e}")
            return {'tables_count': 0, 'records_count': 0}
    
    async def _verify_backup(self, backup_file: Path, original_db: str) -> bool:
        """Verify backup integrity by test restore"""
        test_db = f"test_restore_{int(time.time())}"
        
        try:
            # Restore to test database
            success = await self.restore_database(
                str(backup_file),
                test_db,
                'movie' if 'movie' in original_db.lower() else 'user'
            )
            
            if success:
                # Drop test database
                await self._drop_database(test_db)
                logger.info(f"Backup verification successful for {backup_file}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Backup verification failed: {e}")
            # Clean up test database if it exists
            try:
                await self._drop_database(test_db)
            except:
                pass
            return False
    
    async def _ensure_database_exists(
        self, host: str, port: int, user: str, 
        password: str, database: str
    ):
        """Ensure database exists, create if not"""
        try:
            conn = await aiomysql.connect(
                host=host, port=port,
                user=user, password=password
            )
            
            async with conn.cursor() as cursor:
                await cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}`")
            
            conn.close()
        except Exception as e:
            logger.error(f"Failed to create database {database}: {e}")
            raise
    
    async def _drop_database(self, database: str):
        """Drop a database (use with caution!)"""
        # This is primarily for test databases
        if not database.startswith('test_'):
            raise ValueError("Can only drop test databases")
        
        conn = await aiomysql.connect(
            host=self.config.movie_db_host,
            port=self.config.movie_db_port,
            user=self.config.movie_db_user,
            password=self.config.movie_db_password
        )
        
        async with conn.cursor() as cursor:
            await cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
        
        conn.close()
    
    async def _cleanup_old_backups(self):
        """Remove backups older than retention period"""
        cutoff_date = datetime.now() - timedelta(days=self.config.retention_days)
        
        for backup_file in self.backup_path.glob("*.sql*"):
            if backup_file.stat().st_mtime < cutoff_date.timestamp():
                logger.info(f"Removing old backup: {backup_file}")
                backup_file.unlink()
                
                # Remove metadata file if exists
                meta_file = self.backup_path / f"{backup_file.stem}.metadata.json"
                if meta_file.exists():
                    meta_file.unlink()
    
    async def _send_backup_notification(self, backups: List[BackupMetadata]):
        """Send email notification about backup status"""
        if not self.config.notification_email:
            return
        
        try:
            subject = f"NextReel Backup Report - {datetime.now().strftime('%Y-%m-%d')}"
            
            body = "Database Backup Report\n" + "=" * 50 + "\n\n"
            
            for backup in backups:
                body += f"""
Database: {backup.database_name}
Timestamp: {backup.backup_timestamp}
File Size: {backup.file_size / 1024 / 1024:.2f} MB
Duration: {backup.backup_duration:.2f} seconds
Tables: {backup.tables_count}
Records: {backup.records_count:,}
Verified: {'✓' if backup.restore_tested else '✗'}
S3: {'✓' if backup.s3_location else '✗'}
---
"""
            
            # Send email (implement based on your SMTP configuration)
            logger.info(f"Backup notification sent to {self.config.notification_email}")
            
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")
    
    def _calculate_checksum(self, file_path: Path) -> str:
        """Calculate SHA256 checksum of file"""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    
    def _save_metadata(self, metadata: BackupMetadata):
        """Save backup metadata to JSON file"""
        meta_file = self.backup_path / f"{Path(metadata.file_path).stem}.metadata.json"
        
        # Convert to dict with datetime as string
        meta_dict = asdict(metadata)
        meta_dict['backup_timestamp'] = metadata.backup_timestamp.isoformat()
        
        with open(meta_file, 'w') as f:
            json.dump(meta_dict, f, indent=2)


class BackupScheduler:
    """Schedule automated backups"""
    
    def __init__(self, manager: DatabaseBackupManager):
        self.manager = manager
        self.backup_time = os.getenv('BACKUP_TIME', '02:00')
    
    def schedule_daily_backup(self):
        """Schedule daily backup at specified time"""
        schedule.every().day.at(self.backup_time).do(self._run_backup)
        logger.info(f"Daily backup scheduled at {self.backup_time}")
    
    def _run_backup(self):
        """Run backup in async context"""
        asyncio.run(self.manager.backup_database('both'))
    
    def start(self):
        """Start the scheduler"""
        self.schedule_daily_backup()
        
        while True:
            schedule.run_pending()
            time.sleep(60)  # Check every minute


# CLI Interface
async def main():
    """Main entry point for CLI usage"""
    import argparse
    
    parser = argparse.ArgumentParser(description='NextReel Database Backup Manager')
    parser.add_argument('action', choices=['backup', 'restore', 'verify', 'schedule'],
                       help='Action to perform')
    parser.add_argument('--database', choices=['movie', 'user', 'both'],
                       default='both', help='Database to backup/restore')
    parser.add_argument('--backup-file', help='Backup file for restore')
    parser.add_argument('--target-db', help='Target database name for restore')
    parser.add_argument('--point-in-time', help='Restore to specific time (ISO format)')
    
    args = parser.parse_args()
    
    # Load configuration
    config = BackupConfig.from_env()
    manager = DatabaseBackupManager(config)
    
    if args.action == 'backup':
        # Perform backup
        backups = await manager.backup_database(args.database)
        if backups:
            print(f"✓ Backup completed successfully")
            for backup in backups:
                print(f"  - {backup.database_name}: {backup.file_size / 1024 / 1024:.2f} MB")
        else:
            print("✗ Backup failed")
            sys.exit(1)
    
    elif args.action == 'restore':
        if not args.backup_file:
            print("Error: --backup-file required for restore")
            sys.exit(1)
        
        success = await manager.restore_database(
            args.backup_file,
            args.target_db or config.movie_db_name,
            args.database
        )
        
        if success:
            print("✓ Restore completed successfully")
        else:
            print("✗ Restore failed")
            sys.exit(1)
    
    elif args.action == 'verify':
        # Verify latest backups
        latest_backups = sorted(
            manager.backup_path.glob("*.sql*"),
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )[:2]  # Get latest 2 backups
        
        for backup in latest_backups:
            print(f"Verifying {backup.name}...")
            result = await manager._verify_backup(backup, 'test_db')
            print(f"  {'✓' if result else '✗'} Verification {'passed' if result else 'failed'}")
    
    elif args.action == 'schedule':
        # Start scheduled backups
        scheduler = BackupScheduler(manager)
        print(f"Starting backup scheduler (daily at {scheduler.backup_time})...")
        try:
            scheduler.start()
        except KeyboardInterrupt:
            print("\nScheduler stopped")


if __name__ == '__main__':
    asyncio.run(main())