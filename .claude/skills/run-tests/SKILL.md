---
name: run-tests
description: Run project tests with the right pytest flags — supports specific files, coverage, and full suite
---

# Run Tests

Run the nextreel-lite test suite with consistent configuration.

## Usage

- `/run-tests` — Run the full test suite
- `/run-tests movie_service` — Run tests for a specific module
- `/run-tests --cov` — Run with coverage report

## Execution

### Determine scope

If the user specified a module name (e.g., `movie_service`), map it to the test file:
- `tests/test_{module_name}.py`

If no argument, run the full suite.

### Run the tests

Use the project's venv pytest:

```bash
# Full suite
python3 -m pytest tests/ -v --tb=short --timeout=30

# Specific file
python3 -m pytest tests/test_{module}.py -v --tb=short --timeout=30

# With coverage
python3 -m pytest tests/ --cov=. --cov-report=term-missing --tb=short --timeout=30
```

### Configuration reference

The project uses these pytest settings (from `pyproject.toml`):
- `asyncio_mode = "auto"` — no need for `@pytest.mark.asyncio`
- `--strict-markers` — undefined markers cause errors
- `--tb=short` — condensed tracebacks
- `-q` — quiet output (override with `-v` for verbose)

### Known stub files

These test files are stubs (~135 bytes, just `pytest.skip`). Skip them if running individual tests:
- `test_2024_query.py`
- `test_enhanced_movie_data.py`
- `test_language_filter.py`
- `test_loki_logs.py`
- `test_query_performance.py`
- `test_security_enhanced.py`
- `test_security_integration.py`
- `test_ssl.py`

### Interpreting results

- Report pass/fail counts and any failures with tracebacks.
- If tests fail, identify the root cause and suggest a fix.
- Note any skipped tests and why they were skipped.
