"""Minimal environment bootstrap helpers without package side effects."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ENV: str | None = None
_ENV_LOADED = False
_REPO_ROOT = Path(__file__).resolve().parent


def get_environment() -> str:
    """Return the current environment name (cached after first call)."""
    global _ENV
    if _ENV is None:
        _ENV = os.getenv("NEXTREEL_ENV", os.getenv("FLASK_ENV", "production"))
    return _ENV


def ensure_env_loaded() -> None:
    """Load the appropriate dotenv files exactly once."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return

    env_name = get_environment()
    if env_name == "development":
        load_dotenv(_REPO_ROOT / ".env.development")
        load_dotenv(_REPO_ROOT / ".env")
    else:
        load_dotenv(_REPO_ROOT / ".env.production")
        load_dotenv(_REPO_ROOT / ".env")

    _ENV_LOADED = True


def _reset_environment() -> None:
    """Clear cached bootstrap state for tests."""
    global _ENV, _ENV_LOADED
    _ENV = None
    _ENV_LOADED = False
