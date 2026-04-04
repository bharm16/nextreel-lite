"""SSL certificate validation and context creation for database connections.

Connection testing and the CLI runner live in ``scripts/validate_ssl.py``.
"""

import os
import ssl
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from logging_config import get_logger

logger = get_logger(__name__)

class SSLCertificateValidator:
    """Comprehensive SSL certificate validation for database connections"""
    
    def __init__(self, cert_path: str = None):
        self.cert_path = cert_path
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
            # If no cert path specified, skip validation
            if not self.cert_path:
                results['errors'].append("No certificate file specified - will use system defaults")
                return results
            
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
                
                # Extract certificate information.  Use the timezone-aware
                # ``_utc`` variants introduced in cryptography >= 42.
                not_before = cert.not_valid_before_utc
                not_after = cert.not_valid_after_utc

                sig_algo = (
                    cert.signature_hash_algorithm.name
                    if cert.signature_hash_algorithm
                    else "unknown"
                )

                results["certificate_info"] = {
                    "subject": cert.subject.rfc4514_string(),
                    "issuer": cert.issuer.rfc4514_string(),
                    "not_valid_before": not_before.isoformat(),
                    "not_valid_after": not_after.isoformat(),
                    "serial_number": str(cert.serial_number),
                    "signature_algorithm": sig_algo,
                    "is_valid": datetime.now(timezone.utc) < not_after,
                }

                # Check if this is a CA certificate using BasicConstraints
                # (not string-matching on the subject, which is trivially bypassable).
                try:
                    bc = cert.extensions.get_extension_for_class(x509.BasicConstraints)
                    if bc.value.ca:
                        results["certificate_valid"] = True
                        logger.info("CA certificate validated: %s", results["certificate_info"]["subject"])
                    else:
                        results["errors"].append(
                            "Certificate BasicConstraints.ca is False — not a CA certificate"
                        )
                except x509.ExtensionNotFound:
                    results["errors"].append(
                        "Certificate has no BasicConstraints extension — cannot confirm CA status"
                    )
                
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
            if self.cert_path and os.path.exists(self.cert_path):
                context = ssl.create_default_context(cafile=self.cert_path)
            else:
                # Fall back to system certificates
                context = ssl.create_default_context()
                logger.warning("Using system default certificates")
            
            # MySQL servers use IP-based certs so hostname verification
            # is disabled, but we always verify the certificate chain.
            context.check_hostname = False
            context.verify_mode = ssl.CERT_REQUIRED
            
            # Set minimum TLS version to 1.2
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            
            # Disable weak ciphers
            context.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS')
            
            logger.info("✓ SSL context created with strict security settings")
            return context
            
        except Exception as e:
            logger.error("Failed to create SSL context: %s", e)
            return None
    
    # Connection testing and CLI validation moved to scripts/validate_ssl.py