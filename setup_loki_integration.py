#!/usr/bin/env python3
"""
Complete Loki Integration for NextReel
=======================================

SUMMARY FOR CLAUDE CODE:
This script integrates Loki logging into your NextReel application so that all
logs are automatically sent to Grafana Cloud and can be viewed in dashboards.
It uses your new HTTP API key and ensures logs appear when you query
{application="nextreel"} in Grafana.

HOW TO USE:
1. Save this as: setup_loki_integration.py
2. Run: python3 setup_loki_integration.py
3. This will update your logging configuration and environment
4. Restart your app to start sending logs
5. Query {application="nextreel"} in Grafana to see logs

YOUR NEW CREDENTIALS:
- URL: https://logs-prod-036.grafana.net
- User ID: 1304607
- New API Key: [YOUR_GRAFANA_API_KEY_HERE]
"""

import os
import sys
import time
import json
import requests
from pathlib import Path
from datetime import datetime

# Your NEW Loki HTTP API credentials
LOKI_CONFIG = {
    'GRAFANA_LOKI_URL': 'https://logs-prod-036.grafana.net',
    'GRAFANA_LOKI_USER': '1304607',
    'GRAFANA_LOKI_KEY': '[YOUR_GRAFANA_API_KEY_HERE]'
}

def update_env_file():
    """Update .env.production with Loki credentials"""
    print("="*60)
    print("STEP 1: Updating Environment Configuration")
    print("="*60)
    
    env_file = Path('.env.production')
    if not env_file.exists():
        env_file = Path('.env')
        if not env_file.exists():
            env_file = Path('.env.production')
            env_file.touch()
            print(f"Created {env_file}")
    
    # Read current content
    with open(env_file, 'r') as f:
        lines = f.readlines()
    
    # Update with new credentials
    updated = []
    keys_found = set()
    
    for line in lines:
        if line.strip() and not line.strip().startswith('#'):
            for key in LOKI_CONFIG:
                if line.startswith(f'{key}='):
                    updated.append(f'{key}={LOKI_CONFIG[key]}\n')
                    keys_found.add(key)
                    break
            else:
                updated.append(line)
        else:
            updated.append(line)
    
    # Add missing keys
    for key, value in LOKI_CONFIG.items():
        if key not in keys_found:
            updated.append(f'{key}={value}\n')
    
    with open(env_file, 'w') as f:
        f.writelines(updated)
    
    print(f"✓ Updated {env_file} with Loki credentials")
    return env_file

def create_enhanced_logging_config():
    """Create an enhanced logging configuration that sends to Loki"""
    print("\n" + "="*60)
    print("STEP 2: Creating Enhanced Logging Configuration")
    print("="*60)
    
    logging_config_content = '''"""
Enhanced Logging Configuration with Loki Integration
This sends all NextReel logs to Grafana Cloud
"""
import os
import sys
import logging
import logging.handlers
from pathlib import Path
import json
import requests
import threading
import queue
import time
from datetime import datetime
from dotenv import load_dotenv

# Load environment
load_dotenv('.env.production')
if not os.getenv('GRAFANA_LOKI_URL'):
    load_dotenv('.env')

# Loki configuration
LOKI_URL = os.getenv('GRAFANA_LOKI_URL', 'https://logs-prod-036.grafana.net')
LOKI_USER = os.getenv('GRAFANA_LOKI_USER', '1304607')
LOKI_KEY = os.getenv('GRAFANA_LOKI_KEY', '')

class LokiHandler(logging.Handler):
    """Custom handler to send logs to Grafana Loki"""
    
    def __init__(self, url=None, user=None, key=None):
        super().__init__()
        self.url = url or LOKI_URL
        self.user = user or LOKI_USER
        self.key = key or LOKI_KEY
        self.session = requests.Session()
        self.session.auth = (self.user, self.key)
        self.session.headers.update({'Content-Type': 'application/json'})
        
        # Buffer for batching logs
        self.buffer = queue.Queue(maxsize=1000)
        self.batch_size = 10
        self.flush_interval = 5  # seconds
        
        # Start background thread for sending logs
        self.sender_thread = threading.Thread(target=self._sender_loop, daemon=True)
        self.sender_thread.start()
    
    def emit(self, record):
        """Handle a log record"""
        try:
            # Format the log entry
            log_entry = self.format_log_entry(record)
            
            # Add to buffer (non-blocking)
            try:
                self.buffer.put_nowait(log_entry)
            except queue.Full:
                # If buffer is full, drop oldest and add new
                try:
                    self.buffer.get_nowait()
                    self.buffer.put_nowait(log_entry)
                except:
                    pass
                    
        except Exception as e:
            self.handleError(record)
    
    def format_log_entry(self, record):
        """Format a log record for Loki"""
        # Create labels for the stream
        labels = {
            "application": "nextreel",
            "environment": os.getenv('FLASK_ENV', 'development'),
            "level": record.levelname.lower(),
            "module": record.module,
            "function": record.funcName
        }
        
        # Format the log message
        if record.exc_info:
            message = self.format(record)
        else:
            message = record.getMessage()
        
        # Create timestamp in nanoseconds
        timestamp = str(int(record.created * 1e9))
        
        return {
            "labels": labels,
            "timestamp": timestamp,
            "message": message
        }
    
    def _sender_loop(self):
        """Background thread to batch and send logs"""
        while True:
            try:
                self._flush_batch()
                time.sleep(self.flush_interval)
            except:
                time.sleep(self.flush_interval * 2)  # Back off on error
    
    def _flush_batch(self):
        """Send a batch of logs to Loki"""
        batch = []
        
        # Collect logs from buffer
        while not self.buffer.empty() and len(batch) < self.batch_size:
            try:
                batch.append(self.buffer.get_nowait())
            except queue.Empty:
                break
        
        if not batch:
            return
        
        # Group by labels (streams)
        streams = {}
        for entry in batch:
            labels_key = json.dumps(entry['labels'], sort_keys=True)
            if labels_key not in streams:
                streams[labels_key] = {
                    "stream": entry['labels'],
                    "values": []
                }
            streams[labels_key]['values'].append([
                entry['timestamp'],
                entry['message']
            ])
        
        # Send to Loki
        payload = {"streams": list(streams.values())}
        
        try:
            response = self.session.post(
                f"{self.url}/loki/api/v1/push",
                json=payload,
                timeout=5
            )
            if response.status_code != 204:
                print(f"Loki error: {response.status_code} - {response.text}")
        except Exception as e:
            print(f"Failed to send logs to Loki: {e}")

def setup_logging():
    """Set up logging with Loki integration"""
    # Create logs directory
    log_dir = Path('logs')
    log_dir.mkdir(exist_ok=True)
    
    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    # Clear existing handlers
    root_logger.handlers.clear()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_format = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_format)
    console_handler.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    
    # File handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / 'nextreel.log',
        maxBytes=10_000_000,  # 10MB
        backupCount=5
    )
    file_format = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s:%(funcName)s:%(lineno)d - %(message)s'
    )
    file_handler.setFormatter(file_format)
    file_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    
    # Loki handler
    if LOKI_KEY:
        try:
            loki_handler = LokiHandler()
            loki_format = logging.Formatter('%(message)s')
            loki_handler.setFormatter(loki_format)
            loki_handler.setLevel(logging.INFO)
            root_logger.addHandler(loki_handler)
            print("✓ Loki logging enabled")
        except Exception as e:
            print(f"⚠ Could not enable Loki logging: {e}")
    else:
        print("⚠ Loki API key not found - logs won't be sent to Grafana")
    
    return root_logger

# Create logger for import
logger = logging.getLogger(__name__)

# Auto-setup if imported
setup_logging()
'''
    
    # Save the new logging config
    with open('logging_config.py', 'w') as f:
        f.write(logging_config_content)
    
    print("✓ Created enhanced logging_config.py with Loki integration")
    return True

def test_loki_connection():
    """Test that logs are being sent to Loki"""
    print("\n" + "="*60)
    print("STEP 3: Testing Loki Connection")
    print("="*60)
    
    # Send a test log directly
    url = LOKI_CONFIG['GRAFANA_LOKI_URL']
    user = LOKI_CONFIG['GRAFANA_LOKI_USER']
    key = LOKI_CONFIG['GRAFANA_LOKI_KEY']
    
    # Create test payload
    timestamp = str(int(time.time() * 1e9))
    test_log = {
        "streams": [
            {
                "stream": {
                    "application": "nextreel",
                    "environment": "test",
                    "source": "setup_script"
                },
                "values": [
                    [timestamp, f"Loki integration test at {datetime.now()}"]
                ]
            }
        ]
    }
    
    try:
        response = requests.post(
            f"{url}/loki/api/v1/push",
            auth=(user, key),
            json=test_log,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if response.status_code == 204:
            print("✓ Successfully sent test log to Loki!")
            print("✓ Connection verified")
            return True
        else:
            print(f"✗ Error: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        return False

def create_test_app():
    """Create a test script that generates logs"""
    print("\n" + "="*60)
    print("STEP 4: Creating Test Application")
    print("="*60)
    
    test_app = '''#!/usr/bin/env python3
"""
Test application to verify Loki integration
Run this to generate test logs that should appear in Grafana
"""
import sys
import os
import time
import logging
from datetime import datetime

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the logging configuration
from logging_config import setup_logging

# Get logger
logger = logging.getLogger('nextreel.test')

def generate_test_logs():
    """Generate various log levels for testing"""
    print("\\nGenerating test logs for NextReel...")
    print("These should appear in Grafana when you query: {application=\\"nextreel\\"}")
    print("-" * 60)
    
    # Generate different log levels
    logger.debug(f"DEBUG: Test started at {datetime.now()}")
    logger.info(f"INFO: NextReel test application starting")
    logger.warning(f"WARNING: This is a test warning message")
    logger.error(f"ERROR: This is a test error (not a real error)")
    
    # Simulate some application events
    logger.info("User login simulation: user=testuser")
    logger.info("Movie search: query='The Matrix'")
    logger.info("Recommendation generated: count=10")
    
    # Generate some logs over time
    for i in range(5):
        logger.info(f"Test event {i+1}/5 - Everything working normally")
        time.sleep(2)
    
    logger.info("Test completed successfully!")
    print("\\n✓ Test logs sent to Loki")
    print("\\nNow check Grafana:")
    print("1. Go to: https://bharm16.grafana.net")
    print("2. Navigate to Explore")
    print("3. Select 'grafanacloud-bharm16-logs' data source")
    print("4. Query: {application=\\"nextreel\\"}")
    print("5. You should see all the test logs!")

if __name__ == "__main__":
    generate_test_logs()
'''
    
    with open('test_loki_logs.py', 'w') as f:
        f.write(test_app)
    os.chmod('test_loki_logs.py', 0o755)
    
    print("✓ Created test_loki_logs.py")
    return True

def create_dashboard_json():
    """Create a dashboard configuration for NextReel logs"""
    print("\n" + "="*60)
    print("STEP 5: Creating Dashboard Configuration")
    print("="*60)
    
    dashboard = {
        "dashboard": {
            "title": "NextReel Application Logs",
            "panels": [
                {
                    "title": "Log Volume",
                    "type": "graph",
                    "targets": [{
                        "expr": 'sum(rate({application="nextreel"}[5m])) by (level)'
                    }]
                },
                {
                    "title": "Recent Logs",
                    "type": "logs",
                    "targets": [{
                        "expr": '{application="nextreel"}'
                    }]
                },
                {
                    "title": "Errors",
                    "type": "logs",
                    "targets": [{
                        "expr": '{application="nextreel", level="error"}'
                    }]
                }
            ]
        }
    }
    
    with open('nextreel_logs_dashboard.json', 'w') as f:
        json.dump(dashboard, f, indent=2)
    
    print("✓ Created nextreel_logs_dashboard.json")
    print("  Import this in Grafana to create a logs dashboard")
    return True

def main():
    print("\n" + "="*60)
    print("NEXTREEL LOKI INTEGRATION SETUP")
    print("="*60)
    
    print("\nThis will integrate Loki logging so your logs appear in Grafana")
    print("when you query: {application=\"nextreel\"}")
    
    # Step 1: Update environment
    env_file = update_env_file()
    
    # Step 2: Create enhanced logging config
    create_enhanced_logging_config()
    
    # Step 3: Test connection
    connection_ok = test_loki_connection()
    
    # Step 4: Create test app
    create_test_app()
    
    # Step 5: Create dashboard config
    create_dashboard_json()
    
    # Final instructions
    print("\n" + "="*60)
    print("SETUP COMPLETE!")
    print("="*60)
    
    print("\n✓ Loki integration is configured!")
    
    print("\nTO START SEEING LOGS IN YOUR DASHBOARD:")
    print("-" * 40)
    
    print("\n1. Test the integration:")
    print("   python3 test_loki_logs.py")
    
    print("\n2. Run your NextReel app:")
    print("   FLASK_ENV=production python app.py")
    
    print("\n3. View logs in Grafana:")
    print("   - Go to: https://bharm16.grafana.net")
    print("   - Click 'Explore' (compass icon)")
    print("   - Select data source: 'grafanacloud-bharm16-logs'")
    print("   - Query: {application=\"nextreel\"}")
    print("   - You'll see all your application logs!")
    
    print("\n4. Filter logs by level:")
    print("   - Info: {application=\"nextreel\", level=\"info\"}")
    print("   - Errors: {application=\"nextreel\", level=\"error\"}")
    print("   - Warnings: {application=\"nextreel\", level=\"warning\"}")
    
    print("\n5. Create a dashboard:")
    print("   - Go to Dashboards → Import")
    print("   - Upload nextreel_logs_dashboard.json")
    print("   - Select your Loki data source")
    
    if connection_ok:
        print("\n✅ Your test log was sent successfully!")
        print("   It should already be visible in Grafana")
    else:
        print("\n⚠ Connection test failed - check your credentials")

if __name__ == "__main__":
    main()