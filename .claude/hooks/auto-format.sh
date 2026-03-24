#!/usr/bin/env bash
# PostToolUse hook: Auto-format Python files with Black after edits.
#
# Only runs for Edit/Write tools and only on .py files.
# Uses the project's venv black if available, falls back to system black.

set -uo pipefail

# Only run after file-modifying tools
case "${CLAUDE_TOOL_NAME:-}" in
    Edit|Write) ;;
    *) exit 0 ;;
esac

# Find black — prefer project venv
if [ -x "./venv/bin/black" ]; then
    BLACK="./venv/bin/black"
elif command -v black &>/dev/null; then
    BLACK="black"
else
    exit 0  # No black available, skip silently
fi

# Format only .py files that were modified
for filepath in ${CLAUDE_FILE_PATHS:-}; do
    if [[ "$filepath" == *.py ]] && [ -f "$filepath" ]; then
        $BLACK --quiet --line-length 100 "$filepath" 2>/dev/null || true
    fi
done

exit 0
