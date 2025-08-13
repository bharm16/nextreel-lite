import logging
import logging.handlers
import json
import os
import sys
import time
import re
from datetime import datetime
from typing import Any, Dict, Optional
import traceback
from pathlib import Path

# Grafana Loki handler
try:
    import logging_loki
    LOKI_AVAILABLE = True
except ImportError:
    LOKI_AVAILABLE = False
    print("Warning: python-logging-loki not installed. Run: pip install python-logging-loki")

# Configuration from environment
GRAFANA_LOKI_URL = os.getenv('GRAFANA_LOKI_URL')
GRAFANA_LOKI_USER = os.getenv('GRAFANA_LOKI_USER')
GRAFANA_LOKI_KEY = os.getenv('GRAFANA_LOKI_KEY')


class RedactFilter(logging.Filter):
    """Enhanced logging filter to redact common secret patterns."""

    patterns = ["password", "secret", "api_key", "token", "key"]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            for pattern in self.patterns:
                regex = re.compile(rf"(?i){pattern}[=:\s]+([^\s,]+)")
                message = regex.sub(f"{pattern}=[REDACTED]", message)
            record.msg = message
            record.args = None
        except:
            pass
        return True


class StructuredFormatter(logging.Formatter):
    """Formats logs as structured JSON for better parsing in Loki"""
    
    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            'timestamp': datetime.utcnow().isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
            'environment': os.getenv('FLASK_ENV', 'development'),
        }
        
        # Add correlation ID if available
        if hasattr(record, 'correlation_id'):
            log_obj['correlation_id'] = record.correlation_id
            
        # Add user ID if available
        if hasattr(record, 'user_id'):
            log_obj['user_id'] = record.user_id
        
        # Add any extra fields
        for key, value in record.__dict__.items():
            if key not in ['name', 'msg', 'args', 'created', 'filename', 'funcName',
                          'levelname', 'levelno', 'lineno', 'module', 'msecs',
                          'getMessage', 'pathname', 'process', 'processName', 
                          'relativeCreated', 'thread', 'threadName', 'exc_info', 
                          'exc_text', 'stack_info', 'correlation_id', 'user_id']:
                log_obj[key] = value
                
        if record.exc_info:
            log_obj['exception'] = {
                'type': record.exc_info[0].__name__,
                'message': str(record.exc_info[1]),
                'traceback': ''.join(traceback.format_exception(*record.exc_info))
            }
            
        return json.dumps(log_obj)


def setup_logging(log_level: int = logging.INFO) -> None:
    """Configure application logging with Grafana Cloud integration."""
    
    # Create logs directory
    log_dir = Path('logs')
    log_dir.mkdir(exist_ok=True)
    
    # Standard format for local files
    log_format = "%(asctime)s [%(levelname)s] %(name)s:%(funcName)s: %(message)s"
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(log_format))
    console_handler.addFilter(RedactFilter())
    
    # Rotating file handler
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / 'nextreel.log',
        maxBytes=10_000_000,
        backupCount=5
    )
    file_handler.setFormatter(logging.Formatter(log_format))
    file_handler.addFilter(RedactFilter())
    
    handlers = [console_handler, file_handler]
    
    # Grafana Loki handler (disabled until proper API key is obtained)
    # For Loki integration, you need a specific "Log Push API Key" from Grafana Cloud
    # Go to: https://grafana.com/orgs/[your-org]/api-keys → Create API Key → Role: MetricsPublisher
    loki_enabled = False  # Set to True when you have the correct Loki API key
    
    if LOKI_AVAILABLE and loki_enabled and all([GRAFANA_LOKI_URL, GRAFANA_LOKI_USER, GRAFANA_LOKI_KEY]):
        try:
            # Create Loki handler with authentication
            loki_handler = logging_loki.LokiHandler(
                url=f"{GRAFANA_LOKI_URL}/loki/api/v1/push",
                tags={"application": "nextreel", "environment": os.getenv('FLASK_ENV', 'development')},
                auth=(GRAFANA_LOKI_USER, GRAFANA_LOKI_KEY),
                version="1"
            )
            loki_handler.setFormatter(StructuredFormatter())
            loki_handler.addFilter(RedactFilter())
            handlers.append(loki_handler)
            print("✅ Grafana Loki logging enabled")
        except Exception as e:
            print(f"⚠️  Failed to setup Loki logging: {e}")
    else:
        print("ℹ️  Grafana Loki disabled - using local logging only")
    
    # Configure root logger
    logging.basicConfig(
        level=log_level, 
        handlers=handlers,
        format=log_format
    )
    
    # Reduce noise from libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    
    logging.getLogger(__name__).info(
        "Logging initialized with level: %s", logging.getLevelName(log_level)
    )


def get_logger(name: str) -> logging.Logger:
    """Get or create a logger with enhanced capabilities"""
    
    logger = logging.getLogger(name)
    
    # Skip if already configured via setup_logging
    if len(logging.getLogger().handlers) > 0:
        return logger
    
    # Fallback configuration if setup_logging wasn't called
    level = logging.DEBUG if os.getenv('FLASK_ENV') == 'development' else logging.INFO
    logger.setLevel(level)
    
    # Local file handler
    log_dir = Path('logs')
    log_dir.mkdir(exist_ok=True)
    
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / 'nextreel.log',
        maxBytes=10_000_000,
        backupCount=5
    )
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    file_handler.addFilter(RedactFilter())
    logger.addHandler(file_handler)
    
    # Console handler for development
    if os.getenv('FLASK_ENV') == 'development':
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s'
        ))
        console_handler.addFilter(RedactFilter())
        logger.addHandler(console_handler)
    
    # Grafana Loki handler
    if LOKI_AVAILABLE and all([GRAFANA_LOKI_URL, GRAFANA_LOKI_USER, GRAFANA_LOKI_KEY]):
        try:
            # Create Loki handler with authentication
            loki_handler = logging_loki.LokiHandler(
                url=f"{GRAFANA_LOKI_URL}/loki/api/v1/push",
                tags={"application": "nextreel", "environment": os.getenv('FLASK_ENV', 'development')},
                auth=(GRAFANA_LOKI_USER, GRAFANA_LOKI_KEY),
                version="1"
            )
            loki_handler.setFormatter(StructuredFormatter())
            loki_handler.addFilter(RedactFilter())
            logger.addHandler(loki_handler)
            logger.info("Grafana Loki logging enabled")
        except Exception as e:
            logger.error(f"Failed to setup Loki logging: {e}")
    
    return logger


class CorrelationLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter that adds correlation ID and user ID to log records"""
    
    def process(self, msg, kwargs):
        # Add correlation_id and user_id from context if available
        try:
            from quart import g, session
            if hasattr(g, 'correlation_id'):
                self.extra['correlation_id'] = g.correlation_id
            if 'user_id' in session:
                self.extra['user_id'] = session['user_id']
        except:
            pass
        
        return msg, kwargs


def get_correlation_logger(name: str) -> CorrelationLoggerAdapter:
    """Get a logger that automatically includes correlation ID and user ID"""
    base_logger = get_logger(name)
    return CorrelationLoggerAdapter(base_logger, {})