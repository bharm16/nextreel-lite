#!/usr/bin/env python3
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
    print("\nGenerating test logs for NextReel...")
    print("These should appear in Grafana when you query: {application=\"nextreel\"}")
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
    print("\n✓ Test logs sent to Loki")
    print("\nNow check Grafana:")
    print("1. Go to: https://bharm16.grafana.net")
    print("2. Navigate to Explore")
    print("3. Select 'grafanacloud-bharm16-logs' data source")
    print("4. Query: {application=\"nextreel\"}")
    print("5. You should see all the test logs!")

if __name__ == "__main__":
    generate_test_logs()
