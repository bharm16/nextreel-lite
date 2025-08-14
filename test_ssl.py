#!/usr/bin/env python3
"""
Test script for SSL certificate validation
"""

import asyncio
import sys
import os
from ssl_validator import run_ssl_validation

def main():
    """Main test function"""
    print("SSL Certificate Validation Test")
    print("=" * 50)
    
    # Run the SSL validation
    try:
        success = asyncio.run(run_ssl_validation())
        
        if success:
            print("\n✅ SSL validation completed successfully!")
            return 0
        else:
            print("\n⚠️ SSL validation completed with warnings - review output above")
            return 1
            
    except KeyboardInterrupt:
        print("\n\n⏹️ SSL validation interrupted by user")
        return 130
    except Exception as e:
        print(f"\n❌ SSL validation failed with error: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())