---
name: block-check-hostname-true
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.py$
  - field: new_text
    operator: regex_match
    pattern: check_hostname\s*=\s*True
---

**Blocked: `check_hostname=True` is incompatible with this project's MySQL setup.**

The managed MySQL instance presents an IP-based certificate, so hostname verification will fail. CLAUDE.md documents this explicitly: `ssl.CERT_REQUIRED` is mandatory, but `check_hostname=False` is intentional — not a vulnerability.

If you believe hostname verification should be re-enabled, that is an infrastructure change (new cert with proper SAN) and must be discussed with the user first, not silently introduced in a code edit.

See: `infra/ssl.py`, `infra/pool.py`.
