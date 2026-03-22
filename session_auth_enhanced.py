# DEPRECATED: This module is dead code. All session security is handled by
# session_security_enhanced.py (EnhancedSessionSecurity).
#
# This file is kept only because the filesystem does not allow deletion.
# It should be deleted from version control.
#
# See TECH_DEBT_REPORT.md for details.
raise ImportError(
    "session_auth_enhanced is deprecated. "
    "Use session_security_enhanced.EnhancedSessionSecurity instead."
)
