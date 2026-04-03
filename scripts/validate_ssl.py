#!/usr/bin/env python3
"""CLI tool for SSL certificate and database connection validation.

The ``SSLCertificateValidator`` class (certificate file parsing and SSL
context creation) remains in ``infra/ssl.py``.  This script contains
only the interactive CLI runner and the connection-testing helpers that
are integration-test-like functionality.
"""

import asyncio
import os
import sys

import aiomysql

from logging_config import get_logger

logger = get_logger(__name__)


async def test_database_ssl_connection(validator, config: dict) -> dict:
    """Test SSL connection to database."""
    results = {
        "connection_successful": False,
        "ssl_enabled": False,
        "ssl_cipher": None,
        "ssl_version": None,
        "server_info": {},
        "errors": [],
    }

    connection = None
    try:
        ssl_context = validator.create_ssl_context()
        if not ssl_context:
            results["errors"].append("Failed to create SSL context")
            return results

        logger.info("Testing SSL connection to %s:%s", config["host"], config["port"])

        connection = await aiomysql.connect(
            host=config["host"],
            port=config["port"],
            user=config["user"],
            password=config["password"],
            db=config["database"],
            ssl=ssl_context,
            connect_timeout=10,
        )

        results["connection_successful"] = True

        async with connection.cursor() as cursor:
            await cursor.execute("SELECT VERSION()")
            version = await cursor.fetchone()
            results["server_info"]["version"] = version[0] if version else "Unknown"

            await cursor.execute("SHOW STATUS LIKE 'Ssl_cipher'")
            ssl_status = await cursor.fetchone()

            if ssl_status and ssl_status[1]:
                results["ssl_enabled"] = True
                results["ssl_cipher"] = ssl_status[1]

                await cursor.execute("SHOW STATUS LIKE 'Ssl_version'")
                ssl_version = await cursor.fetchone()
                if ssl_version:
                    results["ssl_version"] = ssl_version[1]

                await cursor.execute("SHOW VARIABLES LIKE 'require_secure_transport'")
                ssl_required = await cursor.fetchone()
                results["server_info"]["ssl_required"] = (
                    ssl_required[1] == "ON" if ssl_required else False
                )

                logger.info(
                    "SSL connection established using %s (%s)",
                    results["ssl_cipher"],
                    results["ssl_version"],
                )
            else:
                results["errors"].append("Connection established but SSL not active")
                logger.warning("Connection established but SSL not active")

    except aiomysql.Error as e:
        results["errors"].append(f"Database connection error: {e}")
        logger.error("Database connection failed: %s", e)
    except Exception as e:
        results["errors"].append(f"Unexpected error: {e}")
        logger.error("Unexpected error: %s", e)
    finally:
        if connection:
            connection.close()

    return results


async def test_non_ssl_connection(config: dict) -> dict:
    """Test that non-SSL connections are rejected in production."""
    results = {"non_ssl_rejected": False, "non_ssl_allowed": False, "errors": []}

    connection = None
    try:
        logger.info("Testing non-SSL connection (should fail if SSL is enforced)")

        connection = await aiomysql.connect(
            host=config["host"],
            port=config["port"],
            user=config["user"],
            password=config["password"],
            db=config["database"],
            ssl=None,
            connect_timeout=5,
        )

        results["non_ssl_allowed"] = True
        results["errors"].append(
            "WARNING: Non-SSL connection was allowed - consider enforcing SSL"
        )
        logger.warning("Non-SSL connection was allowed - SSL not enforced on server")

    except aiomysql.Error as e:
        error_msg = str(e).lower()
        if "ssl" in error_msg or "secure" in error_msg or "require_secure_transport" in error_msg:
            results["non_ssl_rejected"] = True
            logger.info("Non-SSL connection properly rejected - SSL enforced")
        else:
            results["errors"].append(f"Connection failed for unexpected reason: {e}")
            logger.warning("Connection failed: %s", e)

    except Exception as e:
        results["errors"].append(f"Unexpected error: {e}")
    finally:
        if connection:
            connection.close()

    return results


async def run_ssl_validation():
    """Main validation script."""
    from settings import Config
    from dotenv import load_dotenv
    from infra.ssl import SSLCertificateValidator

    from config.env import get_environment
    flask_env = get_environment()
    env_file = ".env" if flask_env == "production" else ".env.development"
    load_dotenv(env_file)

    print("\n" + "=" * 60)
    print("DATABASE SSL CERTIFICATE VALIDATION")
    print("=" * 60 + "\n")

    cert_path = Config.get_ssl_cert_path() if hasattr(Config, "get_ssl_cert_path") else None
    validator = SSLCertificateValidator(cert_path)

    # Step 1: Validate Certificate File
    print("Step 1: Validating SSL Certificate File")
    print("-" * 40)
    cert_results = validator.validate_certificate_file()

    if cert_results["certificate_valid"]:
        print(f"  Certificate file valid: {validator.cert_path}")
        print(f"  Subject: {cert_results['certificate_info']['subject']}")
        print(f"  Valid until: {cert_results['certificate_info']['not_valid_after']}")
    else:
        print("  Certificate validation warnings:")
        for error in cert_results["errors"]:
            print(f"  - {error}")
        if cert_results["file_exists"]:
            print("  Note: Certificate file exists but may not be a root CA")

    # Step 2: Test Database Connection with SSL
    print("\nStep 2: Testing Database SSL Connection")
    print("-" * 40)

    db_config = Config.get_db_config()

    print(f"Environment: {flask_env}")
    print(f"Database Host: {db_config['host']}")
    print(f"Database Name: {db_config['database']}")

    ssl_results = await test_database_ssl_connection(validator, db_config)

    if ssl_results["connection_successful"]:
        print("  Database connection successful")
        print(f"  Server: {ssl_results['server_info'].get('version', 'Unknown')}")

        if ssl_results["ssl_enabled"]:
            print("  SSL enabled")
            print(f"  Cipher: {ssl_results['ssl_cipher']}")
            print(f"  Protocol: {ssl_results['ssl_version']}")

            if ssl_results["server_info"].get("ssl_required"):
                print("  SSL Required: Yes (enforced by server)")
            else:
                print("  SSL Required: No (optional on server)")
        else:
            print("  SSL not active on this connection")
    else:
        print("  Database connection failed:")
        for error in ssl_results["errors"]:
            print(f"  - {error}")

    # Step 3: Test SSL Enforcement
    enforcement_results = {}
    if ssl_results["ssl_enabled"]:
        print("\nStep 3: Testing SSL Enforcement")
        print("-" * 40)

        enforcement_results = await test_non_ssl_connection(db_config)

        if enforcement_results["non_ssl_rejected"]:
            print("  SSL enforcement active - non-SSL connections rejected")
        elif enforcement_results["non_ssl_allowed"]:
            print("  SSL enforcement not active - non-SSL connections allowed")
            print("\nTo enforce SSL on your MySQL server, run:")
            print("  SET GLOBAL require_secure_transport = ON;")
        else:
            print("  Could not determine SSL enforcement status")
    elif ssl_results["connection_successful"]:
        print("\nStep 3: SSL Enforcement Check Skipped")
        print("-" * 40)
        print("  SSL not enabled - skipping SSL enforcement test")
    else:
        print("\nStep 3: SSL Enforcement Check Skipped")
        print("-" * 40)
        print("  Database connection failed - cannot test SSL enforcement")

    # Summary
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)

    all_passed = ssl_results.get("ssl_enabled", False)

    if all_passed:
        print("SSL validation passed - database connections can use SSL")
        if not enforcement_results.get("non_ssl_rejected", False):
            print("  Note: SSL is optional on server - consider enforcing it")
    else:
        print("SSL configuration needs attention - review issues above")

    return all_passed


if __name__ == "__main__":
    success = asyncio.run(run_ssl_validation())
    sys.exit(0 if success else 1)
