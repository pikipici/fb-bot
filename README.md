# FB Engagement Assistant

Human-in-the-loop Facebook engagement monitoring and draft response system.

## Quick Start

```bash
# 1. Setup virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt
playwright install chromium

# 3. Configure environment
cp .env.example .env
# Edit .env with your settings

# 4. Initialize database
python -c "from server.database import init_db; init_db()"

# 5. Start Redis (required for Celery)
# Make sure Redis is running on localhost:6379

# 6. Start the API server
uvicorn server.main:app --host 0.0.0.0 --port 8000

# 7. Start Celery worker (separate terminal)
celery -A bot.celery_app worker --loglevel=info

# 8. Start Celery Beat (separate terminal)
celery -A bot.celery_app beat --loglevel=info
```

## Architecture

- `bot/` — Collector, scorer, draft engine, and background workers
- `server/` — FastAPI backend with auth, RBAC, and REST API
- `dashboard/` — React + TypeScript frontend (TBD)
- `deploy/` — Docker, nginx, systemd configs
- `tests/` — Unit and integration tests
- `docs/` — API docs, runbook, security notes

## Key Principles

- No auto-posting: all actions require human approval
- Fallback chain: AI → semi-dynamic → static → manual
- Circuit breaker per target with health scoring
- Rate limiting (global + per-target)
- Structured logging + Sentry monitoring
