#!/usr/bin/env bash
# PostToolUse hook: Quick lint check after Python file edits.
#
# Runs flake8 for critical errors only (syntax, undefined names).
# Non-blocking — reports issues but doesn't fail the tool call.

set -uo pipefail

# Only run after file-modifying tools
case "${CLAUDE_TOOL_NAME:-}" in
    Edit|Write) ;;
    *) exit 0 ;;
esac

# Find flake8 — prefer project venv
if [ -x "./venv/bin/flake8" ]; then
    FLAKE8="./venv/bin/flake8"
elif command -v flake8 &>/dev/null; then
    FLAKE8="flake8"
else
    exit 0  # No flake8 available, skip silently
fi

# Find black — prefer project venv
if [ -x "./venv/bin/black" ]; then
    BLACK="./venv/bin/black"
elif command -v black &>/dev/null; then
    BLACK="black"
else
    BLACK=""
fi

# Check only .py files for critical errors (syntax, undefined names)
# and report black formatting drift (non-blocking).
for filepath in ${CLAUDE_FILE_PATHS:-}; do
    if [[ "$filepath" == *.py ]] && [ -f "$filepath" ]; then
        output=$($FLAKE8 --select=E9,F63,F7,F82 --show-source "$filepath" 2>/dev/null || true)
        if [ -n "$output" ]; then
            echo "Lint issues in $filepath:"
            echo "$output"
        fi

        if [ -n "$BLACK" ]; then
            if ! "$BLACK" --check --quiet --line-length 100 "$filepath" 2>/dev/null; then
                echo "Format drift in $filepath (run: $BLACK --line-length 100 $filepath)"
            fi
        fi
    fi
done

exit 0
