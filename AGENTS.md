# Agent Guidelines

Be direct and critical in your feedback. If you see a better approach, suggest it immediately.

Don't be overly agreeable. Honest technical feedback is more useful than politeness. Challenge weak assumptions when warranted.

## Repo Reality

- This is a Python Quart app with a small Tailwind build step, not a real Next.js app. There is no active `next.config.js`; the operational workflow is Python-first.
- Use Python `3.11` locally. `runtime.txt` pins `python-3.11.11`. CI runs `3.11` and `3.12`.
- `pytest` is configured in `pyproject.toml` to discover tests from `tests/` only. Root-level `test_*.py` files are legacy/staging files unless explicitly moved into `tests/`.
- `npm test` is a placeholder that exits with an error. Do not use it as a verification step.

## Setup

One-shot (creates `venv/`, installs Python + npm deps, runs `build-css`, seeds `.env` from `.env.example` if missing):

```bash
python3 scripts/bootstrap_dev.py
```

Manual equivalent:

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
npm install
```

Copy `.env.example` to `.env` and fill in real values before running anything that needs MySQL, Redis, or TMDB (the bootstrap script copies the template only when `.env` does not exist).

## Primary Workflows

### Run the app

```bash
python app.py
```

Notes:

- The app auto-loads local env defaults through `local_env_setup.py` when `FLASK_ENV != production`.
- When env vars are absent, runtime defaults fall back to MySQL on `127.0.0.1`, Redis at `redis://localhost:6379`, and database name `imdb`.
- `app.py` will search for an open port starting at `5000`.

### Run tests

Fast default:

```bash
pytest
```

Target a file:

```bash
pytest tests/test_app.py
```

CI-style test run:

```bash
pytest tests/ --cov=. --cov-report=term-missing --cov-report=xml --ignore=venv --ignore=node_modules
```

### Lint and type-check

```bash
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics --exclude=venv,node_modules
flake8 . --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics --exclude=venv,node_modules
black .
mypy .
```

### Build frontend assets

One-off build:

```bash
npm run build-css
```

Watch mode:

```bash
npm run watch-css
```

## Maintenance and Data Scripts

Referential integrity check:

```bash
python -m scripts.validate_referential_integrity
```

Populate missing movie slugs:

```bash
python scripts/add_movie_slugs.py
```

Update missing language values from TMDB:

```bash
python scripts/update_languages_from_tmdb.py
```

Bulk update ratings from TSV:

```bash
python scripts/update_ratings.py
```

Inspect the historical 2024 movie coverage audit:

```bash
python scripts/check_2024_movies.py
```

## Environment Caveats

- The main app configuration is centered on `DB_*`, `REDIS_URL` for local Redis, and `UPSTASH_REDIS_HOST` / `UPSTASH_REDIS_PORT` / `UPSTASH_REDIS_PASSWORD` for production Redis.
- `REDIS_PASSWORD` is an optional legacy secret recognized by `infra/secrets.py`, but runtime Redis connectivity prefers `REDIS_URL` or the Upstash variables.
- `setup_production_env.py` is intentionally dead code right now. Do not build workflows around it.

## Change Discipline

- Prefer adding new tests under `tests/`, not at repo root.
- If you touch templates or Tailwind input files, rebuild CSS before finishing.
- If you change database import or cache-refresh logic, run the referential integrity script when the required services are available.
- Match CI commands when validating substantial Python changes. A local green `pytest` run alone is weaker than the actual CI workflow.
