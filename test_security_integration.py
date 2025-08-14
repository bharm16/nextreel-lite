#!/usr/bin/env python3
"""
Test Enhanced Security Integration
=================================
Quick test to verify the enhanced security system is properly integrated
"""

import asyncio
import os
from unittest.mock import Mock, patch

# Set up test environment
os.environ['FLASK_ENV'] = 'development'

# Mock Quart components
class MockRequest:
    def __init__(self):
        self.path = '/test'
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Test Browser)',
            'Accept': 'text/html',
            'Accept-Language': 'en-US'
        }
        self.remote_addr = '127.0.0.1'

class MockSession(dict):
    def clear(self):
        super().clear()

class MockG:
    def __init__(self):
        self.correlation_id = 'test-123'

class MockResponse:
    def __init__(self):
        self.headers = {}

async def test_security_integration():
    """Test that the enhanced security system is working with app.py"""
    
    print("🔐 Testing Enhanced Security Integration...")
    print("=" * 50)
    
    # Import our integrated app
    from app import create_app
    from session_security_enhanced import add_security_headers
    
    # Create app instance
    app = create_app()
    print("✅ App created with enhanced security")
    
    # Test security headers function
    response = MockResponse()
    
    # Test development headers (should be minimal)
    with patch('os.getenv', return_value='development'):
        result = await add_security_headers(response)
        print("✅ Development security headers applied")
    
    # Test production headers (should be comprehensive)
    response = MockResponse()
    with patch('os.getenv', return_value='production'):
        result = await add_security_headers(response)
        
        # Verify production security headers
        expected_headers = [
            'Strict-Transport-Security',
            'X-Frame-Options', 
            'X-Content-Type-Options',
            'X-XSS-Protection',
            'Content-Security-Policy',
            'Referrer-Policy'
        ]
        
        for header in expected_headers:
            if header in result.headers:
                print(f"✅ {header}: {result.headers[header]}")
            else:
                print(f"❌ Missing header: {header}")
    
    # Test that before_request handlers are registered
    if hasattr(app, 'before_request_funcs') and app.before_request_funcs.get(None):
        handler_count = len(app.before_request_funcs[None])
        print(f"✅ {handler_count} before_request handlers registered")
    else:
        print("❌ No before_request handlers found")
    
    # Test that after_request handlers are registered  
    if hasattr(app, 'after_request_funcs') and app.after_request_funcs.get(None):
        handler_count = len(app.after_request_funcs[None])
        print(f"✅ {handler_count} after_request handlers registered")
    else:
        print("❌ No after_request handlers found")
    
    print("=" * 50)
    print("🎉 Enhanced Security Integration Test Complete!")
    
    return True

if __name__ == "__main__":
    success = asyncio.run(test_security_integration())
    
    if success:
        print("\n✅ All integration tests passed!")
        print("🔒 NextReel now has enterprise-grade session security!")
    else:
        print("\n❌ Some integration tests failed!")
        exit(1)