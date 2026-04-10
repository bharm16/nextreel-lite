"""Backward-compatible shim for the old monolithic route module."""

import sys

from nextreel.web import routes as _module

sys.modules[__name__] = _module
