# Phase J: Randomized Scan Cadence + Self-Rescheduling

> **Status:** Active. Trigger: bos minta scan FB feed lebih random (sometimes lebih
> cepat dari 30 min) untuk reduce predictable inter-scan delta yang bisa di-track
> FB anti-bot.
>
> **Predecessor:** Phase I (Session Hardening) — closed via I-C followup manual
> noVNC login + Playwright Chromium (BREAKTHROUGH terverify ScannerRun id=312).
>
> **Decision (bos approval 2026-05-15):** Range `random.uniform(10min, 25min)` =
> 600..1500s, mean ~17.5 min (lebih agresif dari sekarang fixed 30 min). Pattern:
> **self-rescheduling task** — `scan_all_sources` di akhir-nya enqueue dirinya
> sendiri dengan `apply_async(countdown=random.uniform(MIN, MAX))`.
>
> **Deploy mode:** Opsi B — deploy sekarang sambil observation #4 jalan.
> Variabel observation #4 jadi multi-variable (manual login + cadence baru),
> trade-off accepted.

## Context (current state)

- HEAD `02ff142` di main, server synced.
- Beat schedule sekarang (`bot/celery_app.py`):
  ```
  scan-all-sources: schedule=_scan_interval()  # default 1800s (30 min)
  ```
- Task `scan_all_sources` di `bot/tasks.py:627` bind=True, max_retries=2.
- Existing jitter di `bot/tasks.py:422-440`:
  - `_sleep_startup_jitter` 0-120s (kept, bantu reduce on-the-second alignment)
  - `_sleep_inter_source` 30-90s (kept, between sources)
- Phase I-D test guardrail expect `_scan_interval() >= 1500` — **harus relax/hapus**
  (test akan fail kalau bound bawah Phase J = 600s).

## Goals

- **Tetap aktif**: max interval 25 menit (lebih cepet dari sekarang 30 min), bukan
  lebih jarang.
- **Lebih random**: setiap cycle pilih countdown baru, range 10-25 min.
- **Self-rescheduling**: task chain via `apply_async(countdown=..)` di akhir tiap
  scan (success / fail), bukan beat tick fixed.
- **Beat tetap ada sebagai safety net**: kalau task chain putus (worker crash mid-task,
  Redis hilang, dll), beat watchdog re-arm. Watchdog cheap query: kalau last
  ScannerRun finished_at > MAX_IDLE_SECONDS lalu, kick scan_all_sources.
- **Observation friendliness**: `ScannerRun` row tetap satu per scan (no schema change),
  trigger field expanded to support `"selfsched"` value (selain `"beat"` / `"manual"`).

## Design

### J-1: Config knobs di `bot/celery_app.py`

```python
def _scan_min_interval() -> int:
    return int(os.getenv("SCAN_MIN_INTERVAL_SECONDS", "600"))   # 10 min default

def _scan_max_interval() -> int:
    return int(os.getenv("SCAN_MAX_INTERVAL_SECONDS", "1500"))  # 25 min default

def _scan_watchdog_interval() -> int:
    return int(os.getenv("SCAN_WATCHDOG_INTERVAL_SECONDS", "300"))  # 5 min default
```

Beat schedule:
- **Hapus** `"scan-all-sources"` entry (no longer beat-driven for normal scans).
- **Tambah** `"scan-watchdog"` entry → `bot.tasks.scan_watchdog`, schedule 300s.

Old `_scan_interval()` di-keep tapi deprecated (no caller). Bisa hapus di refactor terpisah.

**Test (J-1):**
- env override `SCAN_MIN_INTERVAL_SECONDS=900` returns 900
- env override `SCAN_MAX_INTERVAL_SECONDS=1200` returns 1200
- defaults: min=600, max=1500
- bounds invariant test: `_scan_min_interval() < _scan_max_interval()` at default values

### J-2: Self-rescheduling di `bot/tasks.py:scan_all_sources`

Pattern (tambah di akhir task, setelah `_finalize_scanner_run`):

```python
def _enqueue_next_scan(*, source: str = "selfsched") -> float | None:
    """Schedule next scan_all_sources with random countdown.

    Returns the chosen countdown (in seconds) for testability/logging.
    Returns None if SCAN_SELFSCHED_DISABLED env flag is set (rollback).
    """
    if os.getenv("SCAN_SELFSCHED_DISABLED") == "1":
        logger.info("scan self-rescheduling DISABLED via env, skipping reschedule")
        return None

    from bot.celery_app import _scan_min_interval, _scan_max_interval
    import random

    countdown = random.uniform(_scan_min_interval(), _scan_max_interval())
    scan_all_sources.apply_async(
        kwargs={"trigger": source},
        countdown=countdown,
    )
    logger.info(
        "scan self-rescheduled: next in %.1fs (%.1f min) trigger=%s",
        countdown, countdown / 60.0, source,
    )
    return countdown
```

Wire ke task body (setelah finalize, both happy + crash path):

```python
@app.task(bind=True, max_retries=2, default_retry_delay=60)
def scan_all_sources(self, trigger: str = "beat"):
    ...
    try:
        ...
        summary = _run_scan_all_sources(db)
    except Exception as exc:
        ...
        _finalize_scanner_run(run_id, status="failed", error_message=str(exc))
        _enqueue_next_scan()  # <-- self-reschedule even on failure
        return {"aborted": True, "reason": "exception", "error": str(exc)}

    _finalize_scanner_run(...)
    _enqueue_next_scan()  # <-- self-reschedule on success
    return summary
```

**Why even on failure?** Scan pelan-pelan recover lebih sehat dari dead-end task chain
yang harus di-kick by watchdog (delays 5+ menit).

**Trigger value `"selfsched"`** — perlu update `trigger if trigger in ("beat", "manual") else "beat"` di line 648 → expand whitelist:
```python
trigger=trigger if trigger in ("beat", "manual", "selfsched") else "beat",
```

**Test (J-2):**
- `_enqueue_next_scan` returns countdown in [MIN, MAX] (sample 100x)
- `_enqueue_next_scan` calls `scan_all_sources.apply_async(kwargs={trigger:"selfsched"}, countdown=N)`
- env `SCAN_SELFSCHED_DISABLED=1` → returns None, no enqueue
- `scan_all_sources` task body calls `_enqueue_next_scan` after finalize on success path
- `scan_all_sources` task body calls `_enqueue_next_scan` after finalize on exception path
- ScannerRun model accepts `trigger="selfsched"` (no DB validation, just whitelist)

### J-3: Watchdog task `bot/tasks.py:scan_watchdog`

Cheap safety net. Logic:

```python
@app.task
def scan_watchdog():
    """Detect stale scan chain, re-arm if needed.

    Self-rescheduling task chain can break if:
      - worker crashes after _finalize_scanner_run but before _enqueue_next_scan
      - Redis loses the queued message (rare)
      - someone manually purges the celery queue
      - first time after deploy (no scan running yet)

    Strategy: query last ScannerRun. If finished_at > MAX_IDLE_SECONDS ago AND
    no row currently in 'running' state, kick scan_all_sources.
    """
    from server.models import ScannerRun

    MAX_IDLE = int(os.getenv("SCAN_MAX_IDLE_SECONDS", "1800"))  # 30 min

    with _db_session() as db:
        running = db.query(ScannerRun).filter(ScannerRun.status == "running").first()
        if running:
            logger.debug("scan_watchdog: scan currently running (id=%s), skip", running.id)
            return {"action": "skip", "reason": "running"}

        last = (
            db.query(ScannerRun)
            .order_by(ScannerRun.id.desc())
            .first()
        )

    now = datetime.now(timezone.utc)
    if last is None:
        # bootstrap: never scanned — kick once
        scan_all_sources.apply_async(kwargs={"trigger": "watchdog"})
        return {"action": "kick", "reason": "no_history"}

    last_finished = last.finished_at or last.started_at
    # naive datetime guard (some old rows might be naive)
    if last_finished.tzinfo is None:
        last_finished = last_finished.replace(tzinfo=timezone.utc)
    idle = (now - last_finished).total_seconds()

    if idle > MAX_IDLE:
        scan_all_sources.apply_async(kwargs={"trigger": "watchdog"})
        return {"action": "kick", "reason": "stale", "idle_seconds": idle}

    return {"action": "skip", "reason": "fresh", "idle_seconds": idle}
```

**Whitelist `"watchdog"` di line 648** as well:
```python
trigger=trigger if trigger in ("beat", "manual", "selfsched", "watchdog") else "beat",
```

(Keep `"beat"` in whitelist for back-compat — old queued messages from pre-J deploy
might still arrive with that trigger value briefly.)

**Test (J-3):**
- watchdog with no ScannerRun history → kicks scan_all_sources, returns {action:"kick", reason:"no_history"}
- watchdog with stale last run (finished 31 min ago) → kicks, returns {action:"kick", reason:"stale"}
- watchdog with fresh last run (finished 5 min ago) → no kick, returns {action:"skip", reason:"fresh"}
- watchdog with currently-running scan → no kick, returns {action:"skip", reason:"running"}
- watchdog with naive datetime in DB → handles gracefully

### J-4: Update Phase I-D test guardrail

In `tests/test_celery_schedule.py` (or wherever the `>=1500` clamp lives), update:
- Old: `_scan_interval() >= 1500`
- New: assert beat schedule **does not include** `scan-all-sources` key
- New: assert beat schedule **includes** `scan-watchdog` key with schedule == 300
- New: bounds invariant — `_scan_min_interval() < _scan_max_interval()`

### J-5: Update activity log + commit + deploy

After all 4 sub-tasks ship:

1. Activity log entry top of `.kiro/steering/development-behavior.md` (trim to 8).
2. Plan file: `.kiro/steering/phase-j-randomized-cadence.md` (this doc, finalized after merge).
3. Commits (TDD-strict, per sub-task):
   - `test: J-1 RED config knobs + watchdog beat schedule`
   - `feat: J-1 GREEN config knobs + watchdog beat schedule`
   - `test: J-2 RED self-rescheduling _enqueue_next_scan + task wire`
   - `feat: J-2 GREEN self-rescheduling _enqueue_next_scan + task wire`
   - `test: J-3 RED scan_watchdog stale detection`
   - `feat: J-3 GREEN scan_watchdog impl`
   - `test: J-4 update I-D guardrail for new schedule shape`
   - `docs(phase-j): activity log + plan close`
4. Push origin main, server `git pull`, restart `fb-bot-{worker,beat}`.
5. Append `.env`: nothing required by default (defaults applied), but document env
   knobs in plan so bos can tune.
6. Verify deploy: trigger watchdog by `celery_app.send_task("bot.tasks.scan_watchdog")`,
   confirm new ScannerRun w/ trigger="watchdog", then observe self-sched chain via
   `journalctl -u fb-bot-worker -f | grep "scan self-rescheduled"`.

## Rollback

- Set `.env` `SCAN_SELFSCHED_DISABLED=1` → next finished scan won't reschedule.
- Watchdog will re-kick after 30 min idle (max_idle env). Effectively reverts to
  watchdog-driven 30-min cadence.
- Full rollback: `git revert` Phase J commit range, restart services.

## Risks

| Risk | Severity | Mitigation |
| --- | --- | --- |
| Self-sched chain breaks (Redis lost, worker OOM) | Medium | Watchdog every 5 min picks up stale state |
| FB detects more frequent (avg 17.5 min vs 30 min) requests | Medium | Range 10-25 min, jitter still random; if observation flips early, raise SCAN_MIN_INTERVAL_SECONDS at runtime |
| Multi-account future: each account need own chain | Low (single-account MVP) | Defer to Phase K |
| Test guardrail (`>=1500`) breaks existing CI | Low | Update test in J-4 same commit chain |
| Observation #4 contamination (multi-variable) | Accepted (opsi B) | Note in activity log entry |

## Acceptance criteria

1. Full pytest suite passes (target: 715 → ~728+ with new tests).
2. Server pull + restart clean, all 3 services active.
3. After deploy, within 5 min: a `scan_watchdog` task entry visible in worker log.
4. Within 30 min: at least one `scan self-rescheduled: next in N.Ns` log line.
5. ScannerRun rows include trigger values "selfsched" and/or "watchdog".
6. Inter-scan delta (`finished_at[N+1] - finished_at[N]`) varies between 10-25 min
   over 4+ cycles (not fixed ~30 min).

## Out of scope

- Multi-account scan parallelism (Phase K).
- Per-source independent cadence (Phase K+).
- ML-driven adaptive cadence (Phase L+).
- Migration of `_scan_interval()` removal — left in code as deprecated stub.
