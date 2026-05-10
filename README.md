# FB Engagement Assistant

Human-in-the-loop Facebook engagement dashboard. Scans the logged-in feed
(home + pages + groups) for trending posts, scores them, and lets an
admin generate templated draft comments and post them to FB under a
5-comments-per-6-hours rate limit. No auto-posting — the admin clicks
Send per comment.

Stack: FastAPI + SQLAlchemy + Celery + Playwright on the backend,
React + TypeScript + Vite + shadcn/ui on the frontend, SQLite for data
and Redis for the Celery broker.

```
Feed scanner → trending_posts → /trending UI → draft → Send → Playwright → comment_history
                                                             ↑
                                                     rate-limit check
```

## Table of contents

- [Quick start (dev)](#quick-start-dev)
- [Layer 1 — Trending Scanner](#layer-1--trending-scanner)
- [Layer 2 — Comment Draft Assistant](#layer-2--comment-draft-assistant)
- [Architecture](#architecture)
- [Further reading](#further-reading)

## Quick start (dev)

Prereqs: Python 3.10+, Node 20+, Redis.

```bash
# Backend
python3 -m venv venv
source venv/bin/activate              # Linux/WSL
# .\venv\Scripts\activate              # Windows PowerShell
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# Edit .env — the important ones are CREDENTIALS_KEY (fernet) and
# JWT_SECRET_KEY. See docs/DEPLOY.md for the full checklist.

python -c "from server.database import init_db; init_db()"

# Three separate shells:
uvicorn server.main:app --host 0.0.0.0 --port 8100
celery -A bot.celery_app worker --loglevel=info
celery -A bot.celery_app beat   --loglevel=info

# Frontend (another shell)
cd dashboard
npm install
npm run dev     # Vite dev server (proxied to :8100)
# OR for production-like testing:
npm run build   # outputs dashboard/dist/ which FastAPI serves
```

Default login after first `python -c "..."` init is `admin` / `admin123`
(change immediately in prod).

## Layer 1 — Trending Scanner

Celery Beat fires `bot.tasks.collect_all_targets` every 15 minutes →
`source_collector` uses a Playwright session (cookies loaded from the
active FBAccount) to scrape posts from each enabled Source
(home_feed / page / group) → `TrendingPostService.upsert` applies the
per-source keyword filter, drops unsupported URL shapes (Stories, Reels,
Watch), scores the post (reactions/velocity), and inserts a row in
`trending_posts` with `status='NEW'`.

Re-scans preserve admin status (`DRAFTED`, `COMMENTED`, `SKIPPED`) so
the UI doesn't get clobbered. The `/trending` page polls
`GET /api/v1/trending` every 30s.

## Layer 2 — Comment Draft Assistant

Admin flow on the `/trending` page:

1. Click **Generate Draft** — renders the active template
   (`POST /api/v1/trending/{id}/draft`) and flips the post to `DRAFTED`.
2. Edit the textarea to taste.
3. Click **Send** — `POST /api/v1/trending/{id}/comment`.

The send endpoint is the main choke point. It:

- Validates non-empty comment.
- Rejects Stories / Reels / Watch URLs with 415 before any Playwright
  launch (these have no comment composer DOM).
- Calls `RateLimitService.check_allowed()` — 5 comments per rolling 6h
  window. Returns 429 if quota is spent.
- Loads the active FBAccount's cookies (Fernet-decrypted) and launches
  headless Chromium via `bot.modules.comment_sender.send_comment`.
- Multi-locale CSS selector group finds the composer, types each char
  with 50-150ms jitter, clicks Post, waits for the posted-comment
  article node (`Comment by <name>` in EN, `Komentar oleh <name>` in
  ID) to verify.
- On success writes `comment_history(status='SENT')` and the post auto-
  flips to `COMMENTED`. On soft failure writes `FAILED` without quota
  burn. `CookieExpired` marks the FBAccount `EXPIRED`; `Checkpoint`
  marks it `CHECKPOINT`.

The `/history` page shows the full audit trail with status filter +
pagination.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                         Dashboard (React)                             │
│    /trending    /history    /review    /sources    /template         │
└────────┬─────────────────────────────────────────────────────┬───────┘
         │ REST /api/v1                                        │
         ↓                                                      │
┌──────────────────────┐       ┌──────────────────────────────────────┐
│  FastAPI (uvicorn)   │       │  Celery Worker + Beat                │
│  - auth, JWT         │       │  - collect_all_targets (every 15m)   │
│  - /trending         │       │  - weekly_digest (future)            │
│  - /trending/.../*   │       │                                       │
│  - /history          │       │  ┌──────────────────────────────┐    │
│  - /rate-limit       │       │  │ source_collector             │    │
│  - /fb-accounts      │       │  │  (Playwright feed scrape)    │    │
│  - /sources          │       │  └──────────────┬───────────────┘    │
│  - /template         │       │                 ↓                     │
└──────────┬───────────┘       │  ┌──────────────────────────────┐    │
           │                   │  │ TrendingPostService.upsert   │    │
           │ uses              │  │  - keyword filter            │    │
           │                   │  │  - unsupported-URL filter    │    │
           │                   │  │  - score_trending            │    │
           │                   │  └──────────────┬───────────────┘    │
           │                   └─────────────────┼────────────────────┘
           ↓                                     ↓
┌──────────────────────────────────────────────────────────────────────┐
│   SQLite (data/app.db)                        │  Redis (broker)       │
│   users · fb_accounts · sources ·             │                       │
│   trending_posts · comment_history ·          │                       │
│   comment_templates · rate_limit_* (implicit) │                       │
└──────────────────────────────────────────────────────────────────────┘
```

Sender is only invoked synchronously from the `/trending/.../comment`
endpoint — it is intentionally NOT a Celery task so rate-limit and UI
feedback are immediate.

- `bot/` — Playwright modules (`fb_session`, `comment_sender`),
  Celery app + tasks, trending scorer, keyword filter.
- `server/` — FastAPI app, SQLAlchemy models, routers, services,
  auth, crypto (Fernet cookie jar).
- `dashboard/` — React + Vite + Tailwind + shadcn/ui, TanStack Query.
- `tests/` — 594 tests (pytest + pytest-asyncio).
- `scripts/` — real-FB smoke scripts (`probe_comment_dom.py`,
  `smoke_comment_send.py`) — run manually against a real account.

## Further reading

- `docs/ARCHITECTURE.md` — deeper pipeline + data model + decision log.
- `docs/DEPLOY.md` — server setup + runbook (systemd, rebuild, rollback).
- `AGENTS.md` / `LOCAL_AI_CONTEXT.md` — AI-agent context for contributors.
