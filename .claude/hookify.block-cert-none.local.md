---
name: block-cert-none
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.py$
  - field: new_text
    operator: contains
    pattern: CERT_NONE
---

**Blocked: ssl.CERT_NONE is forbidden in this project.**

This project requires `ssl.CERT_REQUIRED` for all database connections. `CERT_NONE` disables certificate verification and is a security vulnerability.

Note: `check_hostname=False` is intentional (MySQL uses IP-based certs) — that is NOT the same as disabling cert verification.
