# Architecture

Deeper look at the scanner → send pipeline, the data model, and the
major design decisions that shaped the MVP. Start with the top-level
flow diagram in the main [README](../README.md) and return here when
you need detail.

## Pipelines

### Scan pipeline (Layer 1 — read-only)

```
Celery Beat (15m tick)
    │
    ▼
bot.tasks.collect_all_targets
    │   loads enabled Source rows
    │   fan-outs to one task per target
    ▼
bot.tasks._collect_targets (async)
    │   reuses one Playwright browser
    │   loads the active FBAccount cookies
    ▼
bot.modules.source_collector
    │   scrapes N raw post dicts per source (feed/page/group)
    │   normalises into {fb_post_id, author_*, text, post_url,
    │                   likes, comments, shares, post_timestamp, ...}
    ▼
server.services.trending_post_service.TrendingPostService.upsert
    │   1. matches_keyword_filter(include/exclude)
    │   2. classify_unsupported_post_url()  ← Stories/Reels/Watch
    │   3. score_trending(post)              ← velocity + reactions
    │   4. idempotent upsert by fb_post_id
    │        - insert  → status='NEW'
    │        - update  → refresh metrics, preserve admin status
    ▼
trending_posts  (SQLite)
```

The scanner is intentionally read-only. It never posts, likes, or
modifies FB state — that's Layer 2's job.

#### Idempotency

Re-scans every 15m must not clobber admin intent. The upsert preserves
`status` when it's one of `DRAFTED`, `COMMENTED`, `SKIPPED`. Only
`NEW` rows are allowed to churn. This means a `SKIPPED` Stories row
stays skipped forever, and a `DRAFTED` row's `text_snippet` is
refreshed but its user-written draft state (tracked separately in the
UI / draft table) is untouched.

#### Unsupported-URL filter

Facebook Stories, Reels, Watch, and Share/Video permalinks do NOT
render a comment composer in the DOM. Early versions let them into
`trending_posts` and the admin would click Send, Playwright would hunt
for the composer textbox for ~60s, and finally error with
"Comment composer ga ketemu". Now they are rejected at three layers:

1. `TrendingPostService.upsert` drops them before insert.
2. `POST /api/v1/trending/{id}/comment` returns 415 if one slips in.
3. `_serialize()` on the list payload exposes `unsupported_kind` so
   the UI renders a Badge and disables Generate/Send.

Source of truth is [`server/utils/fb_url.py`](../server/utils/fb_url.py).

### Send pipeline (Layer 2 — human-gated write)

```
Admin clicks Send in Trending UI
    │
    ▼
POST /api/v1/trending/{id}/comment  (server.routers.trending.send_post_comment)
    │
    ├── validate post exists + status not COMMENTED
    ├── reject unsupported post_url (415)
    ├── RateLimitService.check_allowed() → 429 if 5/6h exhausted
    ├── pick active FBAccount, decrypt cookies (Fernet)
    │
    ▼
bot.modules.comment_sender.send_comment  (async, Playwright)
    │
    │   1. launch headless Chromium
    │   2. create_session_context with cookies
    │   3. page.goto(post_url), detect /login or /checkpoint redirects
    │   4. _find_composer() — grouped multi-locale CSS selector:
    │        [aria-label^="Comment as" | "Komen sebagai" |
    │         "Tulis komentar" | "Write a comment" |
    │         "Write a public comment"]
    │        Falls back to per-locale wait_for_selector loop.
    │   5. _type_humanlike() — 50-150ms per-char jitter via
    │        page.keyboard.type (triggers React state properly).
    │   6. _find_post_button() — grouped ID/EN button aria-labels.
    │   7. _wait_posted_comment() — grouped verification node
    │        aria-label prefix ("Comment by" / "Komentar oleh").
    │
    ├── SENT        → record_send(status='SENT', fb_comment_id?) →
    │                 CommentHistory row + auto-flip post to COMMENTED
    ├── FAILED soft → record_send(status='FAILED', error_message=...)
    │                 (no quota burn, no post status flip) → 502
    ├── CookieExpired → mark FBAccount EXPIRED, log FAILED, 503
    ├── Checkpoint  → mark FBAccount CHECKPOINT, log FAILED, 503
    └── CommentSendError (input validation) → 400
```

`RateLimitService` uses a rolling `sent_at` window filtered to
`status='SENT'`. Non-SENT attempts don't count against quota, which
is why a composer-not-found result is classified "soft failure" (502)
rather than a quota-burning success.

## Data model

Tables live in `server/models.py`. Highlights:

- **users** — admin / viewer / ops roles, bcrypt-hashed password,
  refresh tokens stored in JWT cookie.
- **fb_accounts** — Fernet-encrypted cookie jar, label, status
  (`ACTIVE` | `EXPIRED` | `CHECKPOINT` | `DISABLED`), last_seen.
  Single-account MVP but schema ready for multi.
- **sources** — what to scan. `type` is `home_feed` | `page` | `group`,
  `fb_entity_id` is the FB numeric id for page/group (ignored for
  home_feed), `keywords_include` / `keywords_exclude` are JSON arrays,
  `enabled` gates the scanner.
- **trending_posts** — one row per unique `fb_post_id` seen; metrics
  refreshed on re-scan; `status` advances `NEW → DRAFTED → COMMENTED`
  (or `SKIPPED`).
- **comment_templates** — MVP has a single active row with
  `template_text` (Jinja-style placeholders like `{{author_name}}`).
  Admin edits via `/template`.
- **comment_history** — immutable audit trail. `status` is
  `SENT` | `FAILED` | `PENDING`. `fb_comment_id` best-effort (FB
  often returns null). `sent_at` is the rolling-window key.

Foreign keys: `comment_history.trending_post_id → trending_posts.id`
ON DELETE CASCADE; `.user_id → users.id` ON DELETE SET NULL.

## Key decisions

### No auto-posting

Every send requires a human click. The backend has no endpoint to
mass-send queued drafts. This is deliberate — FB aggressively flags
automated engagement, and the bot's job is to save keystrokes, not
replace the admin.

### 5 comments / 6 hours hardcoded

Chosen conservatively for a fresh cookie-session account. Configurable
later (it's already a constant in `RateLimitService` — lift to env var
or DB settings when multi-account lands). Per-char typing jitter
50-150ms adds another layer of humanness.

### Cookie session, not Graph API

Graph API requires app review for any publish_actions scope, which FB
no longer grants to non-business partners. Cookie session with a real
logged-in account behaves like a browser and needs no approval, at the
cost of cookie-expiry breakage (handled via `CookieExpiredError` →
UI prompt to re-login).

### Playwright over requests-html / selenium

Playwright's async API + Chromium bundle + built-in aria-label
selectors + clean headless mode won. The composer DOM is React-rendered
with virtualised comment lists, which rules out static HTML scraping.

### Multi-locale selectors

Earlier selectors hardcoded English aria-labels. Accounts with
Indonesian UI (the default for this project's user base) would silently
fail with "composer not found". The fix is a grouped CSS selector with
both EN + ID variants plus a per-locale fallback loop. See
`bot/modules/comment_sender.py` constants `_TEXTBOX_SELECTOR`,
`_POST_COMMENT_BUTTON`, `_POSTED_COMMENT_PREFIXES`.

### Sync send endpoint, not Celery

The Send flow is user-triggered and needs immediate feedback
(rate-limit, cookie status, send result, quota update). A Celery task
would force the UI to poll for outcome. Running the Playwright call
inline inside the FastAPI request handler is slower (3-10s typical) but
keeps the UX synchronous. The router is async so uvicorn's worker pool
isn't blocked.

### SQLite for MVP

One logged-in user, one FB account, low write volume. WAL mode handles
the scanner's concurrent writes with the API's reads. Migration to
Postgres is a schema-level change only; SQLAlchemy abstracts the rest.

## Testing

594 tests total (`pytest`). Layout:

- `tests/test_*_router.py` — FastAPI endpoints via TestClient with
  fresh SQLite per fixture.
- `tests/test_*_service.py` — service-level logic (rate limiting,
  keyword filter, trending scorer, upsert).
- `tests/test_comment_sender.py` — Playwright mocks with async
  side_effects routing selectors by substring.
- `tests/test_celery_tasks.py` — task integration tests.

Real-FB smoke scripts live in `scripts/` and are excluded from the
default test run — run them manually against a real account when
tuning DOM selectors or verifying end-to-end.

## What's not here (yet)

- Multi-account rotation
- Semi-dynamic / static template fallback chain
- Sentry / structured logging
- Admin password self-reset flow
- Proper audit log for admin actions (currently only comment sends are
  logged to `comment_history`)
