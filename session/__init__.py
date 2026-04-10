"""Session package helpers with lazy submodule exposure."""

from importlib import import_module

_LAZY_SUBMODULES = {
    "keys",
    "quart_session_compat",
    "user_auth",
}


def __getattr__(name: str):
    if name in _LAZY_SUBMODULES:
        module = import_module(f"session.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module 'session' has no attribute {name!r}")


__all__ = sorted(_LAZY_SUBMODULES)
