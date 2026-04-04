"""
Enhanced Logging Configuration with Loki Integration
This sends all NextReel logs to Grafana Cloud
"""
import logging
import logging.handlers
import json
import os
import queue
import sys
import threading
from pathlib import Path

from env_bootstrap import ensure_env_loaded, get_environment

_LOGGING_CONFIGURED = False

class LokiHandler(logging.Handler):
    """Custom handler to send logs to Grafana Loki"""
    
    def __init__(self, url=None, user=None, key=None):
        super().__init__()
        ensure_env_loaded()
        self.url = url or os.getenv('GRAFANA_LOKI_URL', 'https://logs-prod-036.grafana.net')
        self.user = user or os.getenv('GRAFANA_LOKI_USER', '1304607')
        self.key = key or os.getenv('GRAFANA_LOKI_KEY', '')
        import requests
        self.session = requests.Session()
        # Use headers directly instead of session.auth to avoid credential leaks in tracebacks
        import base64 as _b64
        _creds = _b64.b64encode(f"{self.user}:{self.key}".encode()).decode()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Authorization': f'Basic {_creds}',
        })
        
        # Buffer for batching logs
        self.buffer = queue.Queue(maxsize=1000)
        self.batch_size = 100
        self.flush_interval = 2  # seconds
        self._dropped_logs = 0
        self._flush_lock = threading.Lock()

        self._stop_event = threading.Event()

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
                self._dropped_logs += 1
                try:
                    self.buffer.get_nowait()
                    self.buffer.put_nowait(log_entry)
                except (queue.Empty, queue.Full):
                    pass
                    
        except Exception as e:
            self.handleError(record)
    
    def format_log_entry(self, record):
        """Format a log record for Loki"""
        # Create labels for the stream
        labels = {
            "application": "nextreel",
            "environment": get_environment(),
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
        """Background thread to batch and send logs."""
        while not self._stop_event.is_set():
            try:
                self._flush_batch()
                self._stop_event.wait(timeout=self.flush_interval)
            except Exception:
                self._stop_event.wait(timeout=self.flush_interval * 2)
        # Final drain on shutdown
        self._flush_batch()

    def close(self):
        """Stop the sender thread and drain remaining logs."""
        self._stop_event.set()
        self.sender_thread.join(timeout=5)
    
    def _flush_batch(self):
        """Send a batch of logs to Loki"""
        with self._flush_lock:
            self._flush_batch_inner()

    def _flush_batch_inner(self):
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
    global _LOGGING_CONFIGURED
    ensure_env_loaded()

    root_logger = logging.getLogger()
    if _LOGGING_CONFIGURED:
        for handler in root_logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(log_level)
        return root_logger

    # Create logs directory
    log_dir = Path('logs')
    log_dir.mkdir(exist_ok=True)
    
    # Root logger configuration
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
    if os.getenv('GRAFANA_LOKI_KEY'):
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
    _LOGGING_CONFIGURED = True
    return root_logger

# Create logger for import
logger = logging.getLogger(__name__)

def get_logger(name):
    """Get a logger instance for a given name - maintains backward compatibility"""
    return logging.getLogger(name)

# setup_logging() is called explicitly by app.py — not at import time.
# This avoids duplicate handlers, unwanted file-system side-effects in
# tests, and daemon threads started before the app is ready.
