# Fase 4 — Collector + Celery Integration

## Scope
Implementasi collector yang bisa scrape Facebook public pages/groups via Playwright,
fallback ke httpx untuk Graph API mode, lalu wire ke pipeline → orchestrator → DB.

## Tasks

### 4.1 Parser Module (`bot/modules/parser.py`)
- Extract post metadata dari raw HTML/JSON
- Fields: fb_post_id, url, author_name, author_id, text_snippet, timestamp, likes, comments, shares, language
- Normalize ke dict format yang pipeline expect
- Handle edge cases: missing fields, malformed HTML, empty posts

### 4.2 Collector Enhancement (`bot/modules/collector.py`)
- `_collect_via_scrape()`: Playwright headless, scroll page, extract post elements
- `_collect_via_api()`: httpx call ke Graph API endpoint (token-based)
- Random delay between scrolls (anti-detection)
- CAPTCHA/block detection → trigger circuit breaker
- Respect `max_posts_per_run` from target config
- Return normalized post dicts

### 4.3 Celery App + Tasks (`bot/celery_app.py`, `bot/tasks.py`)
- Celery app config (Redis broker)
- `collect_all_targets` task: load targets.json, iterate, call collector
- `process_collected_posts` task: run pipeline + orchestrator on collected posts
- Beat schedule: configurable interval (default 30min)
- Retry policy: max 3, exponential backoff

### 4.4 Target Scheduler (`bot/modules/scheduler.py`)
- Load targets from config
- Filter: enabled=true, not in cooldown, circuit breaker allows
- Sort by priority
- Return ordered list of targets to collect

### 4.5 Integration Wiring
- Collector → Parser → Pipeline → Orchestrator → DB save
- Wire rate_guard + circuit_breaker into collector
- Post-collection: save raw posts to DB, then process through pipeline

### 4.6 Tests
- `tests/test_parser.py` — HTML parsing, edge cases
- `tests/test_collector.py` — mock Playwright, mock httpx, verify flow
- `tests/test_scheduler.py` — target filtering, priority sort
- `tests/test_celery_tasks.py` — task execution with mocks
- `tests/test_integration_collector.py` — end-to-end: collect → score → draft → DB

## Order of Implementation
1. Parser (no external deps, pure logic)
2. Scheduler (pure logic)
3. Collector enhancement (needs parser)
4. Celery app + tasks (needs collector + scheduler)
5. Integration test (needs all above)

## Dependencies to Install on Server
- playwright (+ `playwright install chromium`)
- celery[redis]
- redis

## Notes
- Playwright tests will use mocked browser context (no real FB calls in tests)
- Celery tests use `celery.contrib.pytest` with eager mode
- Real scraping only happens on server, never in tests
