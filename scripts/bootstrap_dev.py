#!/usr/bin/env python3
"""One-time local development bootstrap: venv, pip deps, npm deps, optional .env and CSS.

Run from the repository root::

    python3 scripts/bootstrap_dev.py

Requires Python 3.11+ on PATH as ``python3.11`` or ``python3``. Requires ``npm`` for
Node dependencies and ``npm run build-css`` (skipped if ``npm`` is missing).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = REPO_ROOT / "venv"
REQ_FILE = REPO_ROOT / "requirements.txt"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
ENV_FILE = REPO_ROOT / ".env"
PACKAGE_JSON = REPO_ROOT / "package.json"


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    display = " ".join(cmd)
    print("Running: %s" % display)
    result = subprocess.run(cmd, cwd=cwd or REPO_ROOT)
    if result.returncode != 0:
        sys.exit(result.returncode)


def _host_python_for_venv() -> str:
    """Prefer ``python3.11`` (matches ``runtime.txt``); fall back to any 3.11+."""
    for candidate in ("python3.11", "python3"):
        path = shutil.which(candidate)
        if not path:
            continue
        check = subprocess.run(
            [
                path,
                "-c",
                "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)",
            ],
            capture_output=True,
        )
        if check.returncode == 0:
            if candidate == "python3" and not shutil.which("python3.11"):
                print(
                    "WARN: python3.11 not on PATH; using python3. "
                    "Install 3.11 and recreate venv to match runtime.txt."
                )
            return path
    print("ERROR: Need Python 3.11+ on PATH (try python3.11 or python3).")
    sys.exit(1)


def _venv_python() -> Path:
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def main() -> None:
    os.chdir(REPO_ROOT)

    if not REQ_FILE.is_file():
        print("ERROR: Missing %s" % REQ_FILE)
        sys.exit(1)

    if not VENV_DIR.is_dir():
        py = _host_python_for_venv()
        print("Creating virtualenv at %s using %s" % (VENV_DIR, py))
        _run([py, "-m", "venv", str(VENV_DIR)])
    else:
        print("venv/ already exists; skipping venv creation.")

    vpy = _venv_python()
    if not vpy.is_file():
        print("ERROR: Expected interpreter missing: %s" % vpy)
        sys.exit(1)

    _run([str(vpy), "-m", "pip", "install", "--upgrade", "pip"])
    _run([str(vpy), "-m", "pip", "install", "-r", str(REQ_FILE)])

    npm = shutil.which("npm")
    if PACKAGE_JSON.is_file():
        if not npm:
            print("WARN: npm not on PATH; skipping npm install and build-css.")
        else:
            _run([npm, "install"])
            _run([npm, "run", "build-css"])
    else:
        print("WARN: package.json missing; skipping npm steps.")

    if ENV_FILE.is_file():
        print(".env already exists; skipping copy from .env.example.")
    elif ENV_EXAMPLE.is_file():
        shutil.copy2(ENV_EXAMPLE, ENV_FILE)
        print("Created .env from .env.example — edit it with your real secrets.")
    else:
        print("WARN: .env.example missing; no .env created.")

    print("")
    print("Bootstrap complete. Next:")
    if sys.platform == "win32":
        print("  venv\\Scripts\\activate")
    else:
        print("  source venv/bin/activate")
    print("  python app.py")


if __name__ == "__main__":
    main()
