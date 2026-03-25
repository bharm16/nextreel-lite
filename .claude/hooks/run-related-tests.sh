#!/usr/bin/env bash
# PostToolUse hook: Run related test file after editing a Python source file.
#
# Maps source files to their test counterparts using naming conventions:
#   movie_service.py       → tests/test_movie_service.py
#   movies/query_builder.py → tests/test_query_builder.py
#   infra/cache.py         → tests/test_simple_cache.py (special mapping)
#
# Only runs the single most relevant test file to keep feedback fast.
# Non-blocking — reports failures but doesn't halt Claude.

set -uo pipefail

# Only run after file-modifying tools
case "${CLAUDE_TOOL_NAME:-}" in
    Edit|Write) ;;
    *) exit 0 ;;
esac

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
TESTS_DIR="$PROJECT_DIR/tests"

# Find pytest — prefer project venv
if [ -x "$PROJECT_DIR/venv/bin/python3" ]; then
    PYTEST="$PROJECT_DIR/venv/bin/python3 -m pytest"
elif [ -x "$PROJECT_DIR/venv/bin/pytest" ]; then
    PYTEST="$PROJECT_DIR/venv/bin/pytest"
elif command -v pytest &>/dev/null; then
    PYTEST="pytest"
else
    exit 0  # No pytest available, skip silently
fi

# Special source→test mappings where names don't follow convention
declare -A SPECIAL_MAP=(
    ["infra/cache.py"]="test_simple_cache.py"
    ["routes.py"]="test_routes_extended.py"
    ["movies/tmdb_client.py"]="test_tmdb_client.py"
    ["movies/movie.py"]="test_movie_data.py"
    ["movies/query_builder.py"]="test_query_builder.py"
    ["session/auth.py"]="test_session_auth.py"
    ["session/security.py"]="test_session_security.py"
    ["config/env.py"]="test_config.py"
    ["config/session.py"]="test_config.py"
    ["config/database.py"]="test_config.py"
    ["config/api.py"]="test_config.py"
    ["infra/secrets.py"]="test_secrets_manager.py"
    ["infra/pool.py"]="test_database_pool.py"
    ["infra/metrics.py"]="test_metrics_collector.py"
)

for filepath in ${CLAUDE_FILE_PATHS:-}; do
    # Only process .py source files (not test files, not __init__.py)
    [[ "$filepath" == *.py ]] || continue
    [[ "$filepath" == */tests/* ]] && continue
    [[ "$(basename "$filepath")" == "__init__.py" ]] && continue

    # Get path relative to project dir
    relpath="${filepath#$PROJECT_DIR/}"

    # Check special mapping first
    test_file=""
    if [[ -n "${SPECIAL_MAP[$relpath]+x}" ]]; then
        test_file="$TESTS_DIR/${SPECIAL_MAP[$relpath]}"
    else
        # Convention: module_name.py → tests/test_module_name.py
        basename=$(basename "$filepath" .py)
        test_file="$TESTS_DIR/test_${basename}.py"
    fi

    if [ -f "$test_file" ]; then
        # Check that the test file isn't a stub (> 200 bytes = has real tests)
        file_size=$(wc -c < "$test_file" | tr -d ' ')
        if [ "$file_size" -gt 200 ]; then
            echo "Running related tests: $(basename "$test_file")"
            output=$($PYTEST "$test_file" --tb=short -q --no-header 2>&1) || true
            # Only show output if there were failures
            if echo "$output" | grep -qE "FAILED|ERROR"; then
                echo "$output"
            else
                # Show brief success summary
                echo "$output" | tail -1
            fi
            break  # Only run one test file per hook invocation
        fi
    fi
done

exit 0
