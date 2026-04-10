"""Import-time guardrails for auth workflow code."""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _production_python_files() -> list[Path]:
    return sorted(
        path
        for path in REPO_ROOT.rglob("*.py")
        if "tests" not in path.parts and "venv" not in path.parts and "__pycache__" not in path.parts
    )


def _module_level_imports(tree: ast.AST) -> list[str]:
    imports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "session.user_auth":
            imports.append("from session.user_auth import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "session.user_auth":
                    imports.append("import session.user_auth")
    return imports


def test_no_production_module_imports_session_user_auth_at_import_time():
    offenders: list[str] = []
    for path in _production_python_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        module_level = _module_level_imports(tree)
        if module_level:
            offenders.append(f"{path.relative_to(REPO_ROOT)}: {', '.join(module_level)}")

    assert offenders == []
