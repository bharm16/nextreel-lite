"""Tests for infra.ssl SSL context construction."""

import ssl as ssl_lib

from infra.ssl import build_mysql_ssl_context, SSLCertificateValidator


def test_build_mysql_ssl_context_returns_sslcontext():
    ctx = build_mysql_ssl_context()
    assert isinstance(ctx, ssl_lib.SSLContext)


def test_build_mysql_ssl_context_enforces_cert_required():
    ctx = build_mysql_ssl_context()
    assert ctx.verify_mode == ssl_lib.CERT_REQUIRED


def test_build_mysql_ssl_context_disables_hostname_check():
    # MySQL uses IP-based certs — check_hostname must be False.
    ctx = build_mysql_ssl_context()
    assert ctx.check_hostname is False


def test_build_mysql_ssl_context_min_tls_version():
    ctx = build_mysql_ssl_context()
    assert ctx.minimum_version == ssl_lib.TLSVersion.TLSv1_2


def test_build_mysql_ssl_context_accepts_none_cert_path():
    ctx = build_mysql_ssl_context(None)
    assert isinstance(ctx, ssl_lib.SSLContext)


def test_sslcertificatevalidator_create_ssl_context_still_works():
    v = SSLCertificateValidator()
    ctx = v.create_ssl_context()
    assert ctx.verify_mode == ssl_lib.CERT_REQUIRED
    assert ctx.check_hostname is False
