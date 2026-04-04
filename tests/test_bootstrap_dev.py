"""Tests for scripts/bootstrap_dev.py incremental behavior."""

from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path


def _configure_bootstrap_paths(module, repo: Path) -> None:
    module.REPO_ROOT = repo
    module.VENV_DIR = repo / "venv"
    module.REQ_FILE = repo / "requirements.txt"
    module.ENV_EXAMPLE = repo / ".env.example"
    module.ENV_FILE = repo / ".env"
    module.PACKAGE_JSON = repo / "package.json"
    module.PACKAGE_LOCK = repo / "package-lock.json"
    module.TAILWIND_CONFIG = repo / "tailwind.config.js"
    module.CSS_INPUT = repo / "static" / "css" / "input.css"
    module.CSS_TOKENS = repo / "static" / "css" / "tokens.css"
    module.BOOTSTRAP_STATE = repo / ".cache" / "bootstrap_dev.json"


def _write_repo_files(repo: Path) -> None:
    (repo / "static" / "css").mkdir(parents=True)
    (repo / "venv" / "bin").mkdir(parents=True)
    (repo / "venv" / "bin" / "python").write_text("#!/bin/sh\n")
    (repo / "requirements.txt").write_text("quart==0.19.0\n")
    (repo / "package.json").write_text('{"name":"nextreel-lite"}\n')
    (repo / "package-lock.json").write_text('{"lockfileVersion":3}\n')
    (repo / "tailwind.config.js").write_text("module.exports = {};\n")
    (repo / "static" / "css" / "input.css").write_text("@tailwind utilities;\n")
    (repo / "static" / "css" / "tokens.css").write_text(":root{}\n")
    (repo / ".env").write_text("EXISTS=1\n")
    (repo / ".env.example").write_text("EXAMPLE=1\n")


def _write_matching_state(module) -> None:
    module.BOOTSTRAP_STATE.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "python_hash": module._hash_files([module.REQ_FILE]),
        "node_hash": module._hash_files([module.PACKAGE_JSON, module.PACKAGE_LOCK]),
        "css_hash": module._hash_files([module.CSS_INPUT, module.CSS_TOKENS, module.TAILWIND_CONFIG]),
    }
    module.BOOTSTRAP_STATE.write_text(json.dumps(state))


def test_bootstrap_noops_when_inputs_are_unchanged(tmp_path, monkeypatch):
    module = importlib.import_module("scripts.bootstrap_dev")
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_repo_files(repo)
    _configure_bootstrap_paths(module, repo)
    _write_matching_state(module)

    commands: list[list[str]] = []
    monkeypatch.setattr(module, "_parse_args", lambda: argparse.Namespace(force=False))
    monkeypatch.setattr(module, "_run", lambda cmd, cwd=None: commands.append(cmd))
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)

    previous_cwd = Path.cwd()
    try:
        module.main()
    finally:
        os.chdir(previous_cwd)

    assert commands == []


def test_bootstrap_rebuilds_css_when_css_inputs_change(tmp_path, monkeypatch):
    module = importlib.import_module("scripts.bootstrap_dev")
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_repo_files(repo)
    _configure_bootstrap_paths(module, repo)
    _write_matching_state(module)
    module.CSS_INPUT.write_text("@tailwind components;\n")

    commands: list[list[str]] = []
    monkeypatch.setattr(module, "_parse_args", lambda: argparse.Namespace(force=False))
    monkeypatch.setattr(module, "_run", lambda cmd, cwd=None: commands.append(cmd))
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)

    previous_cwd = Path.cwd()
    try:
        module.main()
    finally:
        os.chdir(previous_cwd)

    assert commands == [["/usr/bin/npm", "run", "build-css"]]


def test_bootstrap_force_reruns_dependency_and_css_steps(tmp_path, monkeypatch):
    module = importlib.import_module("scripts.bootstrap_dev")
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_repo_files(repo)
    _configure_bootstrap_paths(module, repo)
    _write_matching_state(module)

    commands: list[list[str]] = []
    monkeypatch.setattr(module, "_parse_args", lambda: argparse.Namespace(force=True))
    monkeypatch.setattr(module, "_run", lambda cmd, cwd=None: commands.append(cmd))
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)

    previous_cwd = Path.cwd()
    try:
        module.main()
    finally:
        os.chdir(previous_cwd)

    vpy = str(module.VENV_DIR / "bin" / "python")
    assert commands == [
        [vpy, "-m", "pip", "install", "--upgrade", "pip"],
        [vpy, "-m", "pip", "install", "-r", str(module.REQ_FILE)],
        ["/usr/bin/npm", "install"],
        ["/usr/bin/npm", "run", "build-css"],
    ]
