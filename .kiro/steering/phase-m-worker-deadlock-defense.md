# Phase M — Worker Deadlock Defense

**Status:** M-1 + M-2 deployed live (2026-05-18, HEAD `5e601d5`). M-3..M-6 BLOCKED on observation window T+24h ≥ 2026-05-19 12:45 UTC.
**Goal:** harden worker against Playwright pipe deadlock → process never hangs >15 min, watchdog kills both DB row AND worker process
**Trigger:** incident 2026-05-16/17 — worker stuck ~40h, 1304 task backlog, pipeline freeze
**Constraint:** zero behavior change for happy-path, only add bounded timeout + force-kill fallback

## Latar Belakang

Phase J + L udah ngebangun watchdog + duplicate-chain guard di **DB layer**, tapi
defense ini ga nyampe ke **process layer**. Incident 2026-05-16/17 buktiin
bocornya:

- Slot worker #2 (PID 501475) stuck di `asyncio.run(_scan_enabled_sources(...))`
  sejak 2026-05-16 17:55:59 UTC
- Phase L-2 watchdog ✅ berhasil flip `ScannerRun id=457` ke `failed +
  aborted_reason='watchdog_zombie_reap'` di 18:07:08 UTC (12 menit kemudian)
- TAPI worker process Python sendiri **tetep nyangkut** — `asyncio.run()` ga
  pernah balik karena Playwright pipe communication deadlock
- ~6 jam kemudian (2026-05-17 00:12:40), slot #1 (PID 501461) ngambil chain
  baru karena `in_flight` query L-1 ga match (row 457 udah `failed`, bukan
  `running`) → ikut stuck
- Setelah 2 slot stuck, worker ga punya kapasitas → semua task baru numpuk di
  Redis (`celery` queue 1304 entries)
- Beat tetep fire `scan_watchdog` + `auto_comment_watchdog` tiap 5 menit, tapi
  task-nya numpuk di queue yang ga di-consume → defense ga jalan
- Watchdog yang berhasil eksekusi (lewat slot #2 sebelum stuck, atau lewat slot
  yang sempet bebas) cuma flip DB row, **ga kill PID worker**

### py-spy live stack confirms

```
Thread 501461 (idle): "MainThread"
    select (selectors.py:469)         # epoll wait timeout=-1
    _run_once (asyncio/base_events.py:1871)
    run_forever (asyncio/base_events.py:603)
    run_until_complete (asyncio/base_events.py:636)
    run (asyncio/runners.py:44)
    _run_scan_all_sources (bot/tasks.py:624)   # asyncio.run(...)
    scan_all_sources (bot/tasks.py:746)
```

asyncio loop nunggu I/O yang ga akan dateng — Playwright pipe ke headless_shell
kemungkinan dapet partial-write/connection-reset tanpa close, atau Chromium
subprocess freeze tapi pipe-nya masih open. Worker proses udah konsumsi 1d 13h
CPU + 1.8GB RSS.

### Defense layer yang missing

| Layer | Phase J/L coverage | Phase M target |
|-------|--------------------|-----------------|
| DB row state | ✅ L-2 watchdog flip `failed` | unchanged |
| Worker process | ❌ tidak ada | ✅ Celery `time_limit` + revoke SIGKILL |
| Playwright cleanup | ❌ `browser.close()` no timeout | ✅ `asyncio.wait_for` wrap |
| LLM empty response | ❌ langsung FAILED | ✅ retry 1x + skip (jangan FAILED) |
| Redis queue health | ❌ tidak ada | ✅ alert metric kalau backlog > N |

## Decisions

- **`time_limit` hard 15 min** untuk semua task yang touch Playwright
  (`scan_all_sources`, `auto_comment_next`, `send_comment_async`). Hard limit
  → Celery SIGKILL worker proses → cgroup kill semua subprocess termasuk
  Chromium → systemd auto-restart (`Restart=always`).
- **`soft_time_limit` 12 min** — raisable `SoftTimeLimitExceeded` exception,
  task bisa cleanup + finalize ScannerRun ke `failed` dengan
  `aborted_reason='soft_time_limit'`.
- **L-2 reinforcement** — setelah flip DB row ke `failed`, juga
  `app.control.revoke(task_id, terminate=True, signal='SIGKILL')`. Combo
  hard limit + revoke = double defense.
- **`asyncio.wait_for` wrap** — outer 720s timeout di `_scan_enabled_sources`
  + `_send_comment` biar `asyncio.run()` bisa raise `TimeoutError` clean
  sebelum hard `time_limit` kena. Ini fix di app layer, lebih graceful daripada
  SIGKILL.
- **Browser cleanup timeout** — `await asyncio.wait_for(browser.close(),
  timeout=30)` di finally, fallback `os.kill(chromium_pid, SIGKILL)` kalau
  timeout.
- **LLM empty response retry** — 1 retry dengan jitter 2-5s. Kalau tetep empty,
  log + skip (TrendingPost tetep `NEW`, **JANGAN flip ke SKIPPED** — biar bisa
  di-pick lagi cycle berikutnya). Ini behavior change kecil dari Phase K
  default — dokumentasi Phase K ngomong "AI draft errors → flip SKIPPED",
  tapi empty response itu transient bukan fatal.
- **Redis backlog monitor** — `health_check` task tambahin metric
  `redis_celery_queue_length`. Kalau > 50, log WARN. Kalau > 200, log ERROR.
  (Tidak auto-flush, biar visible.)

## Arsitektur

```
[Celery task entry]
  ├─ @celery_app.task(time_limit=900, soft_time_limit=720)
  ├─ try:
  │    └─ asyncio.run(_scan_enabled_sources(...))
  │         └─ asyncio.wait_for(scan_source(...), timeout=720)
  │              └─ Playwright work
  │                   └─ finally: asyncio.wait_for(browser.close(), timeout=30)
  ├─ except SoftTimeLimitExceeded:
  │    └─ finalize ScannerRun status='failed' aborted_reason='soft_time_limit'
  ├─ except TimeoutError:
  │    └─ finalize ScannerRun status='failed' aborted_reason='asyncio_timeout'
  └─ finally:
       └─ _enqueue_next_scan() (chain stays alive)

[scan_watchdog beat 5min]
  └─ for each stale running ScannerRun:
       ├─ flip DB row → 'failed' aborted_reason='watchdog_zombie_reap'
       └─ NEW: app.control.revoke(task_id, terminate=True, signal='SIGKILL')
```

## Tasks (TDD strict, RED→GREEN per sub-task)

### M-1 — Celery hard time_limit + soft_time_limit decorators

`bot/tasks.py`:
- `@celery_app.task(time_limit=900, soft_time_limit=720)` di `scan_all_sources`
- `@celery_app.task(time_limit=600, soft_time_limit=480)` di `auto_comment_next`
- (tambah lebih agresif untuk `auto_comment_next` karena ga ada multi-source loop)

Test (RED → GREEN):
- Task config introspection: `scan_all_sources.time_limit == 900`,
  `auto_comment_next.soft_time_limit == 480`
- Mock long-running coroutine, monkeypatch `SoftTimeLimitExceeded` raise → task
  catch + finalize `ScannerRun.aborted_reason='soft_time_limit'`

### M-2 — `asyncio.wait_for` outer timeout di scan + comment

`bot/tasks.py _run_scan_all_sources`:
```python
results, cookie_expired = asyncio.run(
    asyncio.wait_for(
        _scan_enabled_sources(...),
        timeout=int(os.getenv("SCAN_OUTER_TIMEOUT_SECONDS", "720")),
    )
)
```

`bot/tasks.py auto_comment_next` send_comment call: same pattern with
`COMMENT_OUTER_TIMEOUT_SECONDS=420`.

Test (RED → GREEN):
- Mock `_scan_enabled_sources` sleep 800s (mocked) → catch `TimeoutError` →
  finalize `aborted_reason='asyncio_timeout'`
- Happy path < timeout → unchanged behavior

### M-3 — Browser cleanup timeout di Playwright finally

`bot/modules/source_collector.py` + `bot/modules/comment_sender.py` finally:
```python
try:
    await asyncio.wait_for(context.close(), timeout=15)
except asyncio.TimeoutError:
    logger.warning("context.close() timeout — proceeding to browser.close()")
try:
    await asyncio.wait_for(browser.close(), timeout=30)
except asyncio.TimeoutError:
    logger.error("browser.close() timeout — orphan Chromium possible")
```

Test (RED → GREEN):
- Mock `context.close()` hang → wait_for raise → log warning
- Mock `browser.close()` hang → wait_for raise → log error
- Happy path < timeout → unchanged

### M-4 — Watchdog SIGKILL reinforcement

`bot/tasks.py scan_watchdog` + `auto_comment_watchdog`:
```python
# After flipping DB row to 'failed':
if zombie.task_id:
    try:
        celery_app.control.revoke(
            zombie.task_id, terminate=True, signal="SIGKILL"
        )
        logger.warning(
            "Revoked stuck task_id=%s with SIGKILL", zombie.task_id
        )
    except Exception as exc:
        logger.error("Revoke failed: %s", exc)
```

Test (RED → GREEN):
- Mock zombie row with task_id → watchdog calls `revoke(terminate=True,
  signal='SIGKILL')`
- Mock zombie row without task_id → revoke NOT called, no error
- Mock `revoke` raises → watchdog log error, doesn't propagate

### M-5 — LLM empty response retry + skip (no FAILED)

`bot/tasks.py auto_comment_next` LLM draft step:
```python
try:
    draft = ai_service.generate(...)
    if not draft or not draft.strip():
        # Retry once with small backoff
        await asyncio.sleep(random.uniform(2, 5))
        draft = ai_service.generate(...)
    if not draft or not draft.strip():
        # Still empty after retry — log + skip, keep post NEW for next cycle
        logger.warning(
            "LLM empty response after retry post_id=%s — skipping (post stays NEW)",
            post.id,
        )
        return {"status": "llm_empty", "post_id": post.id}
except Exception as exc:
    # Existing FAILED path for genuine errors
    ...
```

Test (RED → GREEN):
- Mock LLM returns "" once, then valid → comment sent normally
- Mock LLM returns "" both times → return `status='llm_empty'`,
  no `CommentHistory` row created, post stays `NEW`
- Mock LLM raises → existing FAILED path unchanged

### M-6 — Redis backlog metric in health_check

`bot/tasks.py health_check`:
```python
queue_len = redis_client.llen("celery")
if queue_len > 200:
    logger.error("celery backlog CRITICAL: %s", queue_len)
elif queue_len > 50:
    logger.warning("celery backlog WARN: %s", queue_len)
return {
    "status": "healthy",
    "pending_drafts": ...,
    "celery_backlog": queue_len,
}
```

Test (RED → GREEN):
- Mock `redis.llen` 0 → status healthy, backlog 0
- Mock `redis.llen` 75 → log warning, return backlog 75
- Mock `redis.llen` 250 → log error, return backlog 250

## Recovery Procedure (manual, sebelum Phase M deploy)

Sequenced biar safe:

1. **Stop worker** — `sudo systemctl stop fb-bot-worker` (SIGTERM 90s →
   SIGKILL fallback dari systemd default)
2. **Kill orphan Chromium** — `pkill -9 -f headless_shell`,
   `pkill -9 -f playwright/driver/node`
3. **Flush stale queue** — `redis-cli -p 6382 DEL celery` (1304 entries
   expired, semuanya health_check + watchdog yang ga relevan lagi)
4. **Reset stuck DB row** — flip `ScannerRun id=493` ke
   `status='failed' aborted_reason='manual_recovery_phase_m'
   error_message='stuck >34h, recovered manually before phase M deploy'`
5. **Verify Redis empty** — `redis-cli -p 6382 LLEN celery` → 0
6. **Start worker** — `sudo systemctl start fb-bot-worker`
7. **Smoke trigger** — `POST /api/v1/scanner/run-now` via API token,
   verify ScannerRun completes < 5 min, finished cleanly
8. **Watch comment chain** — `auto_comment_next` selfsched should resume
   after first scan finishes (next pick ada di TrendingPost.status='NEW' 468
   entries existing)

## Rollout Plan

1. **M-1 + M-2 first** — Celery time_limit + asyncio.wait_for. Ini paling
   safe (no Playwright code change), defense paling kuat. Deploy + observe
   24h.
2. **M-3** — Playwright finally timeout. Touch core scanner/sender code,
   risk regresi. Deploy after M-1/M-2 stable.
3. **M-4** — Watchdog revoke. Tambahan defense, low risk (cuma additional
   side-effect di watchdog).
4. **M-5** — LLM retry. Independent dari deadlock issue, paralel.
5. **M-6** — Redis metric. Observability only, low risk.

## Env Knobs

| Env | Default | Purpose |
|-----|---------|---------|
| `SCAN_OUTER_TIMEOUT_SECONDS` | 720 | M-2 outer timeout for scan |
| `COMMENT_OUTER_TIMEOUT_SECONDS` | 420 | M-2 outer timeout for comment |
| `BROWSER_CLOSE_TIMEOUT_SECONDS` | 30 | M-3 browser cleanup timeout |
| `CONTEXT_CLOSE_TIMEOUT_SECONDS` | 15 | M-3 context cleanup timeout |
| `LLM_EMPTY_RETRY_MIN_SLEEP` | 2.0 | M-5 retry backoff min |
| `LLM_EMPTY_RETRY_MAX_SLEEP` | 5.0 | M-5 retry backoff max |
| `REDIS_BACKLOG_WARN` | 50 | M-6 warn threshold |
| `REDIS_BACKLOG_CRITICAL` | 200 | M-6 error threshold |

## Out of Scope

- **Playwright stuck root cause investigation** — kemungkinan FB anti-bot
  inject pipe-stall or Chromium crash + zombie pipe. Phase M defense
  agnostic terhadap penyebab — kalau hang >720s, kill. Investigation FB-side
  bisa Phase N (kalau masih sering trigger setelah M deploy).
- **Worker auto-restart on memory bloat** — `--max-memory-per-child` celery
  knob bisa nambahin defense, tapi belum urgent (bukan root cause incident
  ini).
- **Distributed lock generalization** — Phase L-3 udah pasang Redis fire-lock
  buat auto_comment, tapi belum ada untuk scan_all_sources. Ada layer L-1
  (DB in_flight check) yang berfungsi sama. Phase M ga ubah ini.

## Verification (post-deploy each sub-task)

- Focused test (test_tasks_phase_m_*.py)
- Targeted lint (ruff check files yang berubah)
- Full unit test suite (target: > 832 pass)
- Full lint
- Production build (FastAPI startup test)
- `git diff --check` (no whitespace issues)

## Activity Log Slot

(Buat dipindahin ke `development-behavior.md` setelah deploy)

```
| YYYY-MM-DD | phase M-X deploy live + observation | <summary> |
```
