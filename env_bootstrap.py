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
    """Load the appropriate dotenv files exactly once.

    ``.env`` is loaded *first* as the primary local layer so a developer
    who declares ``FLASK_ENV=development`` (or ``NEXTREEL_ENV=...``)
    inside ``.env`` actually has that value drive environment selection.
    Previously the loader called ``get_environment()`` before any
    ``.env`` file was open, which left both vars unset and silently
    fell back to ``"production"`` — pulling in the dead
    ``.env.production`` overlay regardless of what ``.env`` said.

    Precedence (highest first):
      1. shell exports — ``load_dotenv`` does not override existing
         process env vars, so ``NEXTREEL_ENV=production python ...``
         still wins.
      2. ``.env`` — your local source of truth.
      3. ``.env.<mode>`` — shared per-mode defaults (only fills gaps).
    """
    global _ENV_LOADED, _ENV
    if _ENV_LOADED:
        return

    load_dotenv(_REPO_ROOT / ".env")

    # Bust the get_environment() cache: any earlier call (e.g. during
    # module import) saw a pre-.env world and would otherwise stick.
    _ENV = None
    env_name = get_environment()

    if env_name == "development":
        load_dotenv(_REPO_ROOT / ".env.development")
    else:
        load_dotenv(_REPO_ROOT / ".env.production")

    _ENV_LOADED = True


def _reset_environment() -> None:
    """Clear cached bootstrap state for tests."""
    global _ENV, _ENV_LOADED
    _ENV = None
    _ENV_LOADED = False
