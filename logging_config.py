"""
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

# Load environment based on FLASK_ENV
flask_env = os.getenv('FLASK_ENV', 'development')
if flask_env == 'development':
    load_dotenv('.env.development')
    if not os.getenv('GRAFANA_LOKI_URL'):
        load_dotenv('.env')
else:
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

def setup_logging(log_level=logging.INFO):
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
    console_handler.setLevel(log_level)
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

def get_logger(name):
    """Get a logger instance for a given name - maintains backward compatibility"""
    return logging.getLogger(name)

# Auto-setup if imported
setup_logging()
