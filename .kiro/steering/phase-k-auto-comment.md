# Phase K — Auto-Comment Pipeline

**Status:** in progress (planning)
**Goal:** scan → AI draft → send komen, fully autonomous, mirror Phase J cadence
**Trigger:** user explicit ask 2026-05-15 (after Phase J deploy + obs#5 baseline)
**Constraint:** max 5 komen / 6h rolling window (existing `RateLimitService`),
random cadence 12-30 min (mean ~21 min), self-rescheduling chain

## Latar Belakang

Phase J ngebawa scanner cadence jadi random 10-25 min. Sekarang user mau lanjut
auto-pipeline: tiap fresh post yang ke-scan, langsung di-draft AI + di-send
sebagai komen. Full auto, no human-in-loop.

Risk-aware: pipeline ini paling risky karena bikin akun **active poster**.
Mitigasi multi-layer:
1. **Cadence random** mirror Phase J (`AUTO_COMMENT_MIN_INTERVAL=720s`,
   `AUTO_COMMENT_MAX_INTERVAL=1800s`, mean ~21 min)
2. **Hard cap rate-limit** — `RateLimitService` udah enforce 5/6h window.
   Cadence over-fire → rate-limit short-circuit ke FAILED tanpa send
3. **Cookie-flip auto-pause** — kalau detect `CookieExpiredError`, set env
   flag in-DB sehingga task skip semua post sampai re-login
4. **Kill-switch** — `AUTO_COMMENT_DISABLED=1` env → task noop

## Arsitektur

```
[scan_all_sources finally] → next selfsched scan (Phase J)
[auto_comment_next finally] → next selfsched comment (Phase K)
[scan-watchdog beat 5min] → kicks scan if stale (Phase J)
[auto-comment-watchdog beat 5min] → kicks comment if stale (Phase K)
```

`auto_comment_next(*, trigger)` Celery task:
1. Pick eligible TrendingPost (FIFO oldest first, status=NEW, no SENT/FAILED CommentHistory)
2. Pre-check rate limit → if not allowed, log skip + reschedule
3. Pick ACTIVE FB account → if none, log skip + reschedule
4. Generate AI draft via `AIDraftService`
5. Send via `comment_sender.send_comment` (persistent profile)
6. Record `CommentHistory`:
   - `SENT` — auto-flips TrendingPost.status=COMMENTED via existing service
   - `FAILED` — keep TrendingPost.status=NEW so retry possible? **DECIDED: flip
     to status=SKIPPED on FAILED so we don't retry-loop the same post**
7. Reschedule next via `_enqueue_next_comment(*, source='selfsched')`

Self-rescheduling pattern identik dengan Phase J — finally-block fires
rescheduling regardless of success/failure.

## Decisions

- **Eligibility filter**: `TrendingPost.status='NEW'` AND no `CommentHistory`
  rows (any status) untuk post itu. Skip COMMENTED + SKIPPED + DRAFTED.
  TrendingPost yang udah pernah SENT/FAILED → ga retry (dedup).
- **FIFO order**: `ORDER BY collected_at ASC LIMIT 1`. Oldest fresh post first.
- **Failed post handling**: flip `status=SKIPPED` so dedup kuat — next pick
  selalu post yang genuinely fresh.
- **No-account / quota-exceeded / no-eligible**: log skip + reschedule normal
  cadence (per user direction "tetap reschedule normal").
- **Cookie-flip detection**: catch `CookieExpiredError` di task, set
  account.status=EXPIRED via existing FBAccountService path. Reschedule TETAP
  jalan tapi next tick bakal short-circuit di "no ACTIVE account" path.
- **AI draft errors** (config / upstream / empty): record FAILED + flip
  post status SKIPPED, reschedule normal.
- **Rate limit**: existing default 5/6h. User confirm mau random < 5/jam,
  6h window kasih buffer (kalau ada burst di awal jam, sisanya rest).

## Tasks (TDD strict, RED→GREEN per sub-task)

### K-1 — Eligibility query (`server/services/auto_comment_service.py`)

`AutoCommentService.pick_next_eligible_post() -> TrendingPost | None`
- Filter: status='NEW' AND `id NOT IN (SELECT trending_post_id FROM comment_history)`
- Order: `collected_at ASC`
- Return None kalau ga ada eligible

Test (RED → GREEN):
- Empty table → None
- Only COMMENTED posts → None
- 1 NEW + 0 history → returns it
- 1 NEW + matching FAILED history → None (dedup includes FAILED)
- 1 NEW + matching SENT history → None
- 2 NEW with different collected_at → returns oldest

### K-2 — Cadence config + watchdog beat (`bot/celery_app.py`)

- `_auto_comment_min_interval()` env `AUTO_COMMENT_MIN_INTERVAL_SECONDS` default 720
- `_auto_comment_max_interval()` env `AUTO_COMMENT_MAX_INTERVAL_SECONDS` default 1800
- Beat schedule: `auto-comment-watchdog` task `bot.tasks.auto_comment_watchdog` every 300s

Test:
- Config knobs respect env override
- Beat schedule includes `auto-comment-watchdog`

### K-3 — `_enqueue_next_comment` helper + `auto_comment_next` task wire (`bot/tasks.py`)

- `_enqueue_next_comment(*, source='selfsched')` — random uniform countdown,
  apply_async with `kwargs={'trigger': source}`
- `auto_comment_next(trigger='selfsched')` Celery task:
  - Pick eligible post via `AutoCommentService`
  - Short-circuit "no eligible" → reschedule + return
  - Pre-check rate limit → "quota exceeded" → reschedule + return
  - Pick ACTIVE account → "no account" → reschedule + return
  - Generate AI draft → catch errors, record FAILED + flip SKIPPED + reschedule
  - Send via `send_comment` → record SENT/FAILED appropriately
  - Always reschedule in finally
- Env kill-switch `AUTO_COMMENT_DISABLED=1` → noop reschedule (not even reschedule, full pause)
- Trigger whitelist `('selfsched', 'watchdog', 'manual')`

Test (lots, autouse fixture stubs):
- Eligibility short-circuit reschedules
- Rate-limit-exceeded reschedules without send
- No-account short-circuit reschedules
- AI draft error → records FAILED + flip SKIPPED
- Send `CookieExpiredError` → records FAILED + flips account EXPIRED
- Send success → records SENT + post auto-flips COMMENTED via RateLimitService
- Self-resched fires in finally on every path
- Kill-switch noop
- Trigger whitelist

### K-4 — `auto_comment_watchdog` task (`bot/tasks.py`)

Mirror `scan_watchdog`:
- Query `CommentHistory` last row (any status, even FAILED counts as "active")
- If no history OR idle > `AUTO_COMMENT_MAX_IDLE_SECONDS` (default 1800):
  → kick `_enqueue_next_comment(source='watchdog')`

Test:
- No history → kicks
- Recent SENT (idle < 1800s) → no-op
- Stale SENT (idle > 1800s) → kicks
- Returns dict {action, reason, [idle_seconds]}

### K-5 — Deploy + obs

- Push commits, server `git pull` + `systemctl restart fb-bot-worker fb-bot-beat`
- Verify beat schedule live: `auto-comment-watchdog` 300s present
- Verify CommentHistory new entries
- Update activity log
- Update obs#5 cron probe to check both scanner AND comment activity

## Configuration Defaults

| Env Var                         | Default | Purpose                                |
|---------------------------------|---------|----------------------------------------|
| `AUTO_COMMENT_MIN_INTERVAL_SECONDS` | 720   | Min selfsched countdown (12 min)       |
| `AUTO_COMMENT_MAX_INTERVAL_SECONDS` | 1800  | Max selfsched countdown (30 min)       |
| `AUTO_COMMENT_MAX_IDLE_SECONDS` | 1800    | Watchdog stale threshold (30 min)      |
| `AUTO_COMMENT_DISABLED`         | unset   | Set to `1` to pause pipeline entirely  |
| `MAX_COMMENTS_PER_WINDOW`       | 5       | Existing rate limit (override OK)      |

## Rollback Plan

1. Set `AUTO_COMMENT_DISABLED=1` in `.env`
2. Restart worker — task immediately stops processing
3. Beat tetap running tapi watchdog jadi noop juga (need to add same env-check
   ke watchdog OR rely on auto_comment_next noop chain)

## Out of Scope (parked)

- Per-source eligibility filter (e.g. skip group_X)
- Custom per-account rate caps
- LLM circuit breaker (consecutive AI failures pause pipeline)
- Quality gate (filter out short/repetitive drafts)
- UI toggle in dashboard for kill-switch (pakai env dulu)
