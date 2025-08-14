#!/usr/bin/env python3
"""
NextReel Optimized Database Backup System
==========================================
Handles large databases with progress monitoring and table-by-table backup

INSTRUCTIONS FOR CLAUDE CODE:
1. Save this as 'backup_manager_optimized.py' 
2. Run directly: python3 backup_manager_optimized.py backup --database movie
3. For testing, use: python3 backup_manager_optimized.py test
4. Monitor progress in real-time with detailed output

This version handles:
- Large databases (4GB+)
- Table-by-table backup with progress
- Timeout prevention
- Selective table backup
- Compressed streaming
"""

import os
import sys
import subprocess
import gzip
import time
import json
import asyncio
import aiomysql
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import shutil
import signal
from contextlib import contextmanager

# Configure environment
os.environ['MYSQL_PWD'] = os.getenv('DB_PASSWORD', 'caching_sha2_password')

class OptimizedBackupManager:
    """Optimized backup manager for large databases"""
    
    def __init__(self):
        self.backup_dir = Path('./backups')
        self.backup_dir.mkdir(exist_ok=True)
        
        # Database configuration
        self.db_config = {
            'host': os.getenv('DB_HOST', '127.0.0.1'),
            'port': int(os.getenv('DB_PORT', 3306)),
            'user': os.getenv('DB_USER', 'root'),
            'password': os.getenv('DB_PASSWORD', 'caching_sha2_password'),
            'movie_db': os.getenv('DB_NAME', 'imdb'),
            'user_db': os.getenv('USER_DB_NAME', 'UserAccounts')
        }
        
        # Tables to exclude from backup (very large or unnecessary)
        self.exclude_tables = [
            'title.akastest',  # 4GB - test data, not needed
            'title.principals', # 2.7GB - can be rebuilt if needed
            'title.episode',   # 1GB - can be rebuilt if needed  
            'name.basics',     # 957MB - can be rebuilt if needed
        ]
        
        # Priority tables (backup these first)
        self.priority_tables = [
            'UserAccounts.*',  # All user account tables
            'title.basics',    # Core movie data
            'title.ratings',   # User ratings
        ]
    
    async def get_database_info(self, database: str) -> Dict:
        """Get database size and table information"""
        try:
            conn = await aiomysql.connect(
                host=self.db_config['host'],
                port=self.db_config['port'],
                user=self.db_config['user'],
                password=self.db_config['password'],
                db=database
            )
            
            async with conn.cursor() as cursor:
                # Get table sizes
                await cursor.execute("""
                    SELECT 
                        table_name,
                        ROUND(((data_length + index_length) / 1024 / 1024), 2) AS size_mb,
                        table_rows
                    FROM information_schema.tables 
                    WHERE table_schema = %s
                    ORDER BY (data_length + index_length) DESC
                """, (database,))
                
                tables = await cursor.fetchall()
                
                # Convert tuples to dicts for easier handling
                table_list = []
                total_size = 0
                for table in tables:
                    table_info = {
                        'table_name': table[0],
                        'size_mb': table[1] if table[1] else 0,
                        'table_rows': table[2] if table[2] else 0
                    }
                    table_list.append(table_info)
                    total_size += table_info['size_mb']
                
                conn.close()
                
                return {
                    'database': database,
                    'total_size_mb': total_size,
                    'tables': table_list,
                    'table_count': len(table_list)
                }
        except Exception as e:
            print(f"Error getting database info: {e}")
            return None
    
    def backup_table(self, database: str, table: str, output_file: Path) -> bool:
        """Backup a single table with progress monitoring"""
        print(f"  Backing up {database}.{table}...", end='', flush=True)
        start_time = time.time()
        
        try:
            # Use mysqldump with optimized settings for large tables
            dump_cmd = [
                'mysqldump',
                f'--host={self.db_config["host"]}',
                f'--port={self.db_config["port"]}',
                f'--user={self.db_config["user"]}',
                '--single-transaction',
                '--quick',  # Don't buffer query, dump directly to output
                '--lock-tables=false',
                '--compress',  # Use compression on the wire
                '--extended-insert',
                '--disable-keys',  # Faster inserts on restore
                '--skip-comments',  # Reduce file size
                database,
                table
            ]
            
            # Stream directly to compressed file
            with gzip.open(output_file, 'wb', compresslevel=6) as gz_file:
                process = subprocess.Popen(
                    dump_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=os.environ.copy()
                )
                
                # Stream output in chunks to prevent memory issues
                bytes_written = 0
                while True:
                    chunk = process.stdout.read(8192)  # 8KB chunks
                    if not chunk:
                        break
                    gz_file.write(chunk)
                    bytes_written += len(chunk)
                    
                    # Show progress
                    if bytes_written % (1024 * 1024) == 0:  # Every MB
                        print('.', end='', flush=True)
                
                process.wait(timeout=300)  # 5 minute timeout per table
                
                if process.returncode != 0:
                    error = process.stderr.read().decode()
                    print(f" ✗ Error: {error}")
                    return False
                
                duration = time.time() - start_time
                file_size_mb = output_file.stat().st_size / 1024 / 1024
                print(f" ✓ ({file_size_mb:.1f}MB in {duration:.1f}s)")
                return True
                
        except subprocess.TimeoutExpired:
            print(f" ✗ Timeout after 5 minutes")
            process.kill()
            return False
        except Exception as e:
            print(f" ✗ Error: {e}")
            return False
    
    def backup_database_incremental(self, database: str) -> Dict:
        """Backup database table by table"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_subdir = self.backup_dir / f"{database}_{timestamp}"
        backup_subdir.mkdir(exist_ok=True)
        
        print(f"\n=== Backing up database: {database} ===")
        print(f"Backup directory: {backup_subdir}")
        
        # Get list of tables
        try:
            result = subprocess.run(
                [
                    'mysql',
                    f'--host={self.db_config["host"]}',
                    f'--port={self.db_config["port"]}',
                    f'--user={self.db_config["user"]}',
                    '-N',  # No headers
                    '-e', f"SHOW TABLES FROM {database}"
                ],
                capture_output=True,
                text=True,
                env=os.environ.copy()
            )
            
            if result.returncode != 0:
                print(f"Error listing tables: {result.stderr}")
                return None
            
            tables = result.stdout.strip().split('\n')
            tables = [t for t in tables if t]  # Remove empty entries
            
            # Filter out excluded tables
            tables_to_backup = []
            for table in tables:
                if table not in self.exclude_tables:
                    tables_to_backup.append(table)
                else:
                    print(f"  Skipping excluded table: {table}")
            
            print(f"Found {len(tables_to_backup)} tables to backup")
            
            # Backup each table
            successful = []
            failed = []
            total_size = 0
            
            for i, table in enumerate(tables_to_backup, 1):
                print(f"[{i}/{len(tables_to_backup)}]", end=' ')
                output_file = backup_subdir / f"{table}.sql.gz"
                
                if self.backup_table(database, table, output_file):
                    successful.append(table)
                    total_size += output_file.stat().st_size
                else:
                    failed.append(table)
            
            # Create manifest file
            manifest = {
                'database': database,
                'timestamp': timestamp,
                'tables_backed_up': successful,
                'tables_failed': failed,
                'total_size_bytes': total_size,
                'total_size_mb': total_size / 1024 / 1024
            }
            
            manifest_file = backup_subdir / 'manifest.json'
            with open(manifest_file, 'w') as f:
                json.dump(manifest, f, indent=2)
            
            print(f"\n=== Backup Summary ===")
            print(f"✓ Successful: {len(successful)} tables")
            print(f"✗ Failed: {len(failed)} tables")
            print(f"Total size: {total_size / 1024 / 1024:.1f} MB")
            print(f"Manifest: {manifest_file}")
            
            return manifest
            
        except Exception as e:
            print(f"Backup failed: {e}")
            return None
    
    def restore_table(self, database: str, backup_file: Path) -> bool:
        """Restore a single table from backup"""
        print(f"  Restoring {backup_file.stem}...", end='', flush=True)
        
        try:
            # Create database if not exists
            subprocess.run(
                [
                    'mysql',
                    f'--host={self.db_config["host"]}',
                    f'--port={self.db_config["port"]}',
                    f'--user={self.db_config["user"]}',
                    '-e', f"CREATE DATABASE IF NOT EXISTS {database}"
                ],
                env=os.environ.copy(),
                check=True
            )
            
            # Restore table
            with gzip.open(backup_file, 'rb') as gz_file:
                process = subprocess.Popen(
                    [
                        'mysql',
                        f'--host={self.db_config["host"]}',
                        f'--port={self.db_config["port"]}',
                        f'--user={self.db_config["user"]}',
                        database
                    ],
                    stdin=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=os.environ.copy()
                )
                
                stdout, stderr = process.communicate(input=gz_file.read())
                
                if process.returncode == 0:
                    print(" ✓")
                    return True
                else:
                    print(" ✗")
                    return False
                    
        except Exception as e:
            print(f" ✗ Error: {e}")
            return False
    
    def restore_database(self, backup_dir: str, database: str = None) -> bool:
        """Restore database from backup directory"""
        backup_path = Path(backup_dir)
        
        if not backup_path.exists():
            print(f"Backup directory not found: {backup_dir}")
            return False
        
        # Load manifest
        manifest_file = backup_path / 'manifest.json'
        if not manifest_file.exists():
            print("Manifest file not found")
            return False
        
        with open(manifest_file, 'r') as f:
            manifest = json.load(f)
        
        db_name = database or manifest['database']
        print(f"\n=== Restoring database: {db_name} ===")
        print(f"From backup: {backup_path}")
        print(f"Tables to restore: {len(manifest['tables_backed_up'])}")
        
        # Restore each table
        successful = 0
        for table_file in backup_path.glob("*.sql.gz"):
            if self.restore_table(db_name, table_file):
                successful += 1
        
        print(f"\n=== Restore Summary ===")
        print(f"✓ Restored: {successful} tables")
        
        return successful > 0
    
    async def test_connection(self) -> bool:
        """Test database connection"""
        try:
            print("Testing database connection...")
            conn = await aiomysql.connect(
                host=self.db_config['host'],
                port=self.db_config['port'],
                user=self.db_config['user'],
                password=self.db_config['password']
            )
            
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT VERSION()")
                version = await cursor.fetchone()
                print(f"✓ Connected to MySQL {version[0]}")
            
            conn.close()
            return True
            
        except Exception as e:
            print(f"✗ Connection failed: {e}")
            return False
    
    def create_backup_script(self):
        """Create a shell script for cron scheduling"""
        script_content = f"""#!/bin/bash
# NextReel Automated Backup Script
# Add to crontab: 0 2 * * * /path/to/backup.sh

cd {Path.cwd()}
source venv/bin/activate 2>/dev/null || true
python3 {__file__} backup --database movie
python3 {__file__} backup --database user

# Clean old backups (older than 30 days)
find {self.backup_dir} -type d -mtime +30 -exec rm -rf {{}} + 2>/dev/null

# Send notification (optional)
echo "Backup completed at $(date)" | mail -s "NextReel Backup Report" admin@example.com
"""
        
        script_file = Path('backup.sh')
        with open(script_file, 'w') as f:
            f.write(script_content)
        
        script_file.chmod(0o755)
        print(f"Created backup script: {script_file}")
        print("Add to crontab with: crontab -e")
        print("Add line: 0 2 * * * /path/to/backup.sh")


async def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Optimized NextReel Backup Manager')
    parser.add_argument('action', choices=['backup', 'restore', 'test', 'info', 'setup'],
                       help='Action to perform')
    parser.add_argument('--database', help='Database name (movie/user/custom)')
    parser.add_argument('--backup-dir', help='Backup directory for restore')
    
    args = parser.parse_args()
    
    manager = OptimizedBackupManager()
    
    if args.action == 'test':
        # Test connection and show database info
        if await manager.test_connection():
            for db in ['imdb', 'UserAccounts']:
                info = await manager.get_database_info(db)
                if info:
                    print(f"\nDatabase: {info['database']}")
                    print(f"Total size: {info['total_size_mb']:.1f} MB")
                    print(f"Tables: {info['table_count']}")
                    print("\nLargest tables:")
                    for table in info['tables'][:5]:
                        print(f"  - {table['table_name']}: {table['size_mb']} MB ({table['table_rows']:,} rows)")
    
    elif args.action == 'backup':
        if args.database == 'movie':
            manager.backup_database_incremental('imdb')
        elif args.database == 'user':
            manager.backup_database_incremental('UserAccounts')
        elif args.database:
            manager.backup_database_incremental(args.database)
        else:
            # Backup both databases
            manager.backup_database_incremental('imdb')
            manager.backup_database_incremental('UserAccounts')
    
    elif args.action == 'restore':
        if not args.backup_dir:
            print("Error: --backup-dir required for restore")
            sys.exit(1)
        manager.restore_database(args.backup_dir, args.database)
    
    elif args.action == 'info':
        # Show backup directory info
        backups = sorted(manager.backup_dir.glob("*/manifest.json"))
        print(f"Found {len(backups)} backups:")
        for manifest_file in backups[-10:]:  # Show last 10
            with open(manifest_file, 'r') as f:
                manifest = json.load(f)
            print(f"  - {manifest['timestamp']}: {manifest['database']} "
                  f"({manifest['total_size_mb']:.1f} MB, "
                  f"{len(manifest['tables_backed_up'])} tables)")
    
    elif args.action == 'setup':
        # Create cron script
        manager.create_backup_script()


if __name__ == '__main__':
    asyncio.run(main())