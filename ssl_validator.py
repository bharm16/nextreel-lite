# ssl_validator.py
import os
import ssl
import socket
import aiomysql
import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import logging
from typing import Optional, Dict, Any
from cryptography import x509
from cryptography.hazmat.backends import default_backend

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SSLCertificateValidator:
    """Comprehensive SSL certificate validation for database connections"""
    
    def __init__(self, cert_path: str = None):
        self.cert_path = cert_path or os.path.join(os.path.dirname(__file__), 'isrgroot.pem')
        self.validation_results = {}
        
    def validate_certificate_file(self) -> Dict[str, Any]:
        """Validate the SSL certificate file"""
        results = {
            'file_exists': False,
            'file_readable': False,
            'certificate_valid': False,
            'certificate_info': {},
            'errors': []
        }
        
        try:
            # Check file existence
            if not os.path.exists(self.cert_path):
                results['errors'].append(f"Certificate file not found: {self.cert_path}")
                return results
            results['file_exists'] = True
            
            # Check file readability
            if not os.access(self.cert_path, os.R_OK):
                results['errors'].append(f"Certificate file not readable: {self.cert_path}")
                return results
            results['file_readable'] = True
            
            # Parse certificate
            with open(self.cert_path, 'rb') as cert_file:
                cert_data = cert_file.read()
                cert = x509.load_pem_x509_certificate(cert_data, default_backend())
                
                # Extract certificate information
                results['certificate_info'] = {
                    'subject': cert.subject.rfc4514_string(),
                    'issuer': cert.issuer.rfc4514_string(),
                    'not_valid_before': cert.not_valid_before.isoformat(),
                    'not_valid_after': cert.not_valid_after.isoformat(),
                    'serial_number': str(cert.serial_number),
                    'signature_algorithm': cert.signature_algorithm_oid._name,
                    'is_valid': datetime.now(timezone.utc) < cert.not_valid_after.replace(tzinfo=timezone.utc)
                }
                
                # Check if it's a valid root certificate (ISRG or other trusted root)
                if "ISRG Root X1" in results['certificate_info']['subject']:
                    results['certificate_valid'] = True
                    logger.info("✓ ISRG Root X1 certificate validated successfully")
                elif "Root" in results['certificate_info']['subject'] or "CA" in results['certificate_info']['subject']:
                    results['certificate_valid'] = True
                    logger.info(f"✓ Root CA certificate validated: {results['certificate_info']['subject']}")
                else:
                    results['errors'].append(f"Certificate may not be a root CA: {results['certificate_info']['subject']}")
                
                # Check certificate validity period
                if not results['certificate_info']['is_valid']:
                    results['errors'].append("Certificate has expired")
                    
        except Exception as e:
            results['errors'].append(f"Certificate validation error: {str(e)}")
            
        return results
    
    def create_ssl_context(self, verify_mode: ssl.VerifyMode = ssl.CERT_REQUIRED) -> Optional[ssl.SSLContext]:
        """Create a properly configured SSL context"""
        try:
            # Try to create context with the certificate file
            if os.path.exists(self.cert_path):
                context = ssl.create_default_context(cafile=self.cert_path)
            else:
                # Fall back to system certificates
                context = ssl.create_default_context()
                logger.warning("Using system default certificates")
            
            # Set strict SSL options for production
            context.check_hostname = False  # MySQL doesn't use hostname verification
            context.verify_mode = verify_mode
            
            # Set minimum TLS version to 1.2
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            
            # Disable weak ciphers
            context.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS')
            
            logger.info("✓ SSL context created with strict security settings")
            return context
            
        except Exception as e:
            logger.error(f"Failed to create SSL context: {e}")
            return None
    
    async def test_database_ssl_connection(self, config: dict) -> Dict[str, Any]:
        """Test SSL connection to database"""
        results = {
            'connection_successful': False,
            'ssl_enabled': False,
            'ssl_cipher': None,
            'ssl_version': None,
            'server_info': {},
            'errors': []
        }
        
        connection = None
        try:
            # Create SSL context
            ssl_context = self.create_ssl_context()
            if not ssl_context:
                results['errors'].append("Failed to create SSL context")
                return results
            
            # Attempt connection with SSL
            logger.info(f"Testing SSL connection to {config['host']}:{config['port']}")
            
            connection = await aiomysql.connect(
                host=config['host'],
                port=config['port'],
                user=config['user'],
                password=config['password'],
                db=config['database'],
                ssl=ssl_context,
                connect_timeout=10
            )
            
            results['connection_successful'] = True
            
            # Get server information
            async with connection.cursor() as cursor:
                # Get MySQL version
                await cursor.execute("SELECT VERSION()")
                version = await cursor.fetchone()
                results['server_info']['version'] = version[0] if version else 'Unknown'
                
                # Check SSL status
                await cursor.execute("SHOW STATUS LIKE 'Ssl_cipher'")
                ssl_status = await cursor.fetchone()
                
                if ssl_status and ssl_status[1]:
                    results['ssl_enabled'] = True
                    results['ssl_cipher'] = ssl_status[1]
                    
                    # Get SSL version
                    await cursor.execute("SHOW STATUS LIKE 'Ssl_version'")
                    ssl_version = await cursor.fetchone()
                    if ssl_version:
                        results['ssl_version'] = ssl_version[1]
                    
                    # Check if SSL is required
                    await cursor.execute("SHOW VARIABLES LIKE 'require_secure_transport'")
                    ssl_required = await cursor.fetchone()
                    results['server_info']['ssl_required'] = ssl_required[1] == 'ON' if ssl_required else False
                    
                    logger.info(f"✓ SSL connection established using {results['ssl_cipher']} ({results['ssl_version']})")
                else:
                    results['errors'].append("Connection established but SSL not active")
                    logger.warning("⚠ Connection established but SSL not active")
                    
        except aiomysql.Error as e:
            results['errors'].append(f"Database connection error: {str(e)}")
            logger.error(f"✗ Database connection failed: {e}")
        except Exception as e:
            results['errors'].append(f"Unexpected error: {str(e)}")
            logger.error(f"✗ Unexpected error: {e}")
        finally:
            if connection:
                connection.close()
                
        return results
    
    async def test_non_ssl_connection(self, config: dict) -> Dict[str, Any]:
        """Test that non-SSL connections are rejected in production"""
        results = {
            'non_ssl_rejected': False,
            'non_ssl_allowed': False,
            'errors': []
        }
        
        connection = None
        try:
            # Attempt connection without SSL
            logger.info("Testing non-SSL connection (should fail if SSL is enforced)")
            
            connection = await aiomysql.connect(
                host=config['host'],
                port=config['port'],
                user=config['user'],
                password=config['password'],
                db=config['database'],
                ssl=None,  # No SSL
                connect_timeout=5
            )
            
            # If we get here, non-SSL connection was allowed
            results['non_ssl_allowed'] = True
            results['errors'].append("WARNING: Non-SSL connection was allowed - consider enforcing SSL")
            logger.warning("⚠ Non-SSL connection was allowed - SSL not enforced on server")
            
        except aiomysql.Error as e:
            # Check if SSL is required
            error_msg = str(e).lower()
            if "ssl" in error_msg or "secure" in error_msg or "require_secure_transport" in error_msg:
                results['non_ssl_rejected'] = True
                logger.info("✓ Non-SSL connection properly rejected - SSL enforced")
            else:
                results['errors'].append(f"Connection failed for unexpected reason: {str(e)}")
                logger.warning(f"Connection failed: {e}")
                
        except Exception as e:
            results['errors'].append(f"Unexpected error: {str(e)}")
        finally:
            if connection:
                connection.close()
                
        return results

async def run_ssl_validation():
    """Main validation script"""
    from settings import Config
    from dotenv import load_dotenv
    
    # Load environment
    flask_env = os.getenv('FLASK_ENV', 'development')
    env_file = '.env' if flask_env == 'production' else '.env.development'
    load_dotenv(env_file)
    
    print("\n" + "="*60)
    print("DATABASE SSL CERTIFICATE VALIDATION")
    print("="*60 + "\n")
    
    # Initialize validator
    cert_path = Config.get_ssl_cert_path() if hasattr(Config, 'get_ssl_cert_path') else 'isrgroot.pem'
    validator = SSLCertificateValidator(cert_path)
    
    # Step 1: Validate Certificate File
    print("Step 1: Validating SSL Certificate File")
    print("-" * 40)
    cert_results = validator.validate_certificate_file()
    
    if cert_results['certificate_valid']:
        print(f"✓ Certificate file valid: {validator.cert_path}")
        print(f"  Subject: {cert_results['certificate_info']['subject']}")
        print(f"  Valid until: {cert_results['certificate_info']['not_valid_after']}")
    else:
        print(f"⚠ Certificate validation warnings:")
        for error in cert_results['errors']:
            print(f"  - {error}")
        if cert_results['file_exists']:
            print("  Note: Certificate file exists but may not be a root CA")
    
    # Step 2: Test Database Connection with SSL
    print("\nStep 2: Testing Database SSL Connection")
    print("-" * 40)
    
    # Get database configuration based on environment
    db_config = Config.get_db_config()
    
    print(f"Environment: {flask_env}")
    print(f"Database Host: {db_config['host']}")
    print(f"Database Name: {db_config['database']}")
    
    # Test SSL connection
    ssl_results = await validator.test_database_ssl_connection(db_config)
    
    if ssl_results['connection_successful']:
        print(f"✓ Database connection successful")
        print(f"  Server: {ssl_results['server_info'].get('version', 'Unknown')}")
        
        if ssl_results['ssl_enabled']:
            print(f"✓ SSL enabled")
            print(f"  Cipher: {ssl_results['ssl_cipher']}")
            print(f"  Protocol: {ssl_results['ssl_version']}")
            
            if ssl_results['server_info'].get('ssl_required'):
                print(f"  SSL Required: Yes (enforced by server)")
            else:
                print(f"  SSL Required: No (optional on server)")
        else:
            print(f"✗ SSL not active on this connection")
    else:
        print(f"✗ Database connection failed:")
        for error in ssl_results['errors']:
            print(f"  - {error}")
    
    # Step 3: Test SSL Enforcement (only if SSL connection worked)
    enforcement_results = {}
    if ssl_results['ssl_enabled']:
        print("\nStep 3: Testing SSL Enforcement")
        print("-" * 40)
        
        enforcement_results = await validator.test_non_ssl_connection(db_config)
        
        if enforcement_results['non_ssl_rejected']:
            print("✓ SSL enforcement active - non-SSL connections rejected")
        elif enforcement_results['non_ssl_allowed']:
            print("⚠ SSL enforcement not active - non-SSL connections allowed")
            print("\nTo enforce SSL on your MySQL server, run:")
            print("  SET GLOBAL require_secure_transport = ON;")
            print("\nOr add to my.cnf/my.ini:")
            print("  [mysqld]")
            print("  require_secure_transport = ON")
        else:
            print("⚠ Could not determine SSL enforcement status")
    elif ssl_results['connection_successful']:
        print("\nStep 3: SSL Enforcement Check Skipped")
        print("-" * 40)
        print("⚠ SSL not enabled - skipping SSL enforcement test")
    else:
        print("\nStep 3: SSL Enforcement Check Skipped")
        print("-" * 40)
        print("ℹ Database connection failed - cannot test SSL enforcement")
        print("  This is normal in development if MySQL is not running")
    
    # Summary
    print("\n" + "="*60)
    print("VALIDATION SUMMARY")
    print("="*60)
    
    all_passed = ssl_results.get('ssl_enabled', False)
    
    if all_passed:
        print("✅ SSL validation passed - database connections can use SSL")
        if not enforcement_results.get('non_ssl_rejected', False):
            print("⚠️  Note: SSL is optional on server - consider enforcing it")
    else:
        print("⚠️ SSL configuration needs attention - review issues above")
        
    # Recommendations
    print("\n📋 RECOMMENDATIONS:")
    print("-" * 40)
    
    if flask_env == 'production':
        print("1. ✓ Running in production mode")
    else:
        print("1. ℹ Running in development mode")
        print("   Set FLASK_ENV=production for production deployment")
    
    if ssl_results.get('ssl_enabled'):
        print("2. ✓ SSL connections working")
    else:
        print("2. ⚠ Enable SSL on your database server")
    
    if enforcement_results.get('non_ssl_rejected'):
        print("3. ✓ SSL enforcement active")
    else:
        print("3. ⚠ Consider enforcing SSL-only connections")
        
    return all_passed

if __name__ == "__main__":
    import sys
    
    # Run the SSL validation
    success = asyncio.run(run_ssl_validation())
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)