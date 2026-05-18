# Development Behavior & Workflow

Panduan behavior pengembangan project ini. Dibaca oleh AI assistant sebelum mulai kerja.

## Identitas Assistant

- Assistant di workspace ini adalah `Apis`; user bukan Apis.
- Refer ke diri sendiri sebagai `Apis` atau `gue` dalam bahasa Indonesia slang.
- User dipanggil `lu`, `bro`, atau `bos` tergantung tone percakapan.
- Tone: supportive, direct, practical — bukan corporate stiff.
- Update pakai frasa casual: `gue cek dulu`, `gue gas`, `udah live`, `aman`.
- Jawaban jelas dan to the point, tapi tetap kayak teammate yang paham konteks project.

## Workflow Development

1. Develop lokal (`D:\program\facebook-bot`) → commit → push ke GitHub.
2. Satu commit per fitur/fix yang jelas, message singkat pakai prefix (`feat:`, `fix:`, `refactor:`, `chore:`).
3. Jangan push langsung ke `main` kalau ada risiko conflict; pakai relay branch kalau perlu.
4. Deploy: SSH ke rdpkhorur → `cd /home/ubuntu/fb-bot && git pull origin main` → restart service.
5. Semua instalasi & runtime (venv, pip, Redis, Playwright, dll) hanya di rdpkhorur. Local = coding + git only.
6. Verifikasi & test dijalankan di rdpkhorur via SSH.
7. Setelah deploy sukses, cleanup relay/temp branch.

## Pola Implementasi Fitur

1. **Living plan dulu** — sebelum coding fitur meaningful, bikin plan file (`.kiro/steering/*.md`). Break jadi task kecil, update status tiap step selesai.
2. **RED/GREEN test-driven** — tulis test yang gagal dulu (RED), baru implementasi sampai pass (GREEN). Ini berlaku buat backend maupun frontend.
3. **Verifikasi bertahap** sebelum commit:
   - Focused test (file/fungsi spesifik yang berubah)
   - Targeted lint (file yang berubah)
   - Full unit test suite
   - Full lint
   - Production build
   - `git diff --check` (no whitespace issues)
4. **Deploy verification** setelah push:
   - SHA match (local/server/remote)
   - Services active
   - Health check OK
   - HTTP 200 di route penting
   - Grep deployed assets buat konfirmasi fitur masuk

## Arsitektur & Separation of Concerns

- **Backend**: handler → service → repository pattern. Logic bisnis di service, data access di repository, HTTP binding di handler.
- **Frontend**: framework-free helper/view-model di `src/lib/*.ts`, component di `src/components/`, service API call di `src/services/`.
- **Public vs Admin**: public API sembunyiin data internal (provider code, rate, error). Admin endpoint boleh expose detail buat ops/audit.
- **Test**: unit test per helper/service, integration test buat flow end-to-end, smoke test buat deploy verification.

## Prinsip Kode

- **Idempotent & non-destructive**: operasi yang bisa ke-trigger ulang (seed, refund, sync) harus aman kalau jalan berkali-kali.
- **Guard before mutate**: lock/check state sebelum operasi yang mengubah data (wallet debit, provider call, status transition).
- **Provider-safe public output**: jangan leak data supplier/provider ke user. Sanitize di layer service/DTO.
- **Explicit over implicit**: persist false/zero values kalau memang intentional, jangan rely on default behavior yang bisa berubah.
- **Incremental & atomic**: satu commit = satu perubahan yang bisa di-revert tanpa break hal lain.

## Safety & Quality Gates

- Tidak deploy sebelum semua verification step pass.
- Tidak touch production DB tanpa guarded/idempotent transaction.
- Pre-existing test failures di-acknowledge tapi tidak block unrelated deploy.
- Stash/backup sebelum operasi risky; pakai `apply` (bukan `pop`) buat safety net.
- Kalau test environment terbatas (misal CGO disabled di Windows), jalanin di server yang capable dan catat hasilnya.

## State Tracking

- Update `.kiro/steering/development-behavior.md` setelah tiap task meaningful.
- Log entry: tanggal, commit hash, deploy status, summary singkat.
- Keep hanya 8 entry terbaru, hapus yang paling lama.
- Kalau sesuatu belum di-deploy, tulis jelas `local only` / `not deployed yet`.

## Current Next Step

> Mutable pointer — ganti tiap kali next decision geser. Activity log di
> bawah tetap immutable history.

**Last shipped:** Phase M-1 + M-2 worker deadlock defense deployed live
(2026-05-18, HEAD `5e601d5`). Celery `time_limit`/`soft_time_limit`
(scan 900/720s, comment 600/480s) + `asyncio.wait_for` outer timeout
(scan 720s, comment 420s). Defends against Playwright pipe deadlock that
bypassed Phase L-2 watchdog (incident 2026-05-16/17: worker stuck >34h).
Recovery: killed orphan Chromium, flushed Redis queue (1390 → 0), reset
ScannerRun id=493. Smoke ScannerRun id=494 success in 81.3s.
Full suite **825 passed**.

**Phase I/J/K/L/M progress:**
- ✅ I-A..I-E (fingerprint, cookie rotation, persistent profile, cadence, stealth)
- ✅ J-1..J-5 (randomized cadence + watchdog + manual login + verification)
- ✅ K-1..K-5 (auto-comment pipeline + dry-run mode)
- ✅ L-1..L-3 (in-flight guard, zombie reaper, fire-lock)
- ✅ M-1 Celery time_limit + soft_time_limit + SoftTimeLimitExceeded handler
- ✅ M-2 asyncio.wait_for outer timeout + asyncio.TimeoutError handler
- ⏳ M-3 Playwright browser/context.close() timeout (next)
- ⏳ M-4 Watchdog SIGKILL revoke reinforcement
- ⏳ M-5 LLM empty response retry + skip
- ⏳ M-6 Redis backlog metric in health_check

**Verification post-deploy (2026-05-18 12:45 UTC):**
- 3 services active (api/worker/beat) ✓
- Time limits live: scan 900/720s, comment 600/480s ✓
- Outer timeouts live: scan 720s, comment 420s ✓
- Smoke ScannerRun id=494: success, 81.3s, no errors ✓
- auto_comment_next chain re-kicked by watchdog (idle 132517s) ✓
- DRAFT id=384 post=347 (dry-run masih aktif) ✓
- Redis queue 0 ✓
- FB account id=1 ACTIVE, failure_count=0 ✓

**Observation #6 status:** baseline cookie 2026-05-14 23:40 UTC. Sekarang
~85h cookie masih ACTIVE — pipeline freeze 2026-05-16/17 BUKAN karena
cookie burn (root cause Playwright pipe deadlock di asyncio loop).
Phase I cookie hardening **likely fixed** pending re-validation post-M.

**Next step:** Observe Phase M defense ~24h, watch for any
`aborted_reason='soft_time_limit'` or `'asyncio_timeout'` dari scan/comment
chain. Kalau muncul → defense kerja, root cause Playwright deadlock
masih ada (lanjut M-3). Kalau ga muncul → 2 minggu observation kondisi
stable, baru consider flip `AUTO_COMMENT_DRY_RUN=0` ke live send.

## Activity Log

| Tanggal | Status | Summary |
|---------|--------|---------|
| 2026-05-18 | phase M-1 + M-2 worker deadlock defense deploy live + obs#6 confirm cookie OK | **Incident root-cause** 2026-05-16/17: 2 slot worker celery stuck di `asyncio.run(_scan_enabled_sources(...))` — `select(timeout=-1)` infinite, py-spy live stack confirms (PID 501461 sejak 2026-05-17 00:12, PID 501475 sejak 2026-05-16 17:55). Phase L-2 watchdog ✅ flip `ScannerRun id=457` ke `failed+aborted='watchdog_zombie_reap'` di 18:07 UTC tapi worker process tetep nyangkut (DB layer doang, ga touch process). Slot kedua 6h kemudian masuk chain baru (id=493) → ikut stuck. Beat fire watchdog tiap 5min konsisten tapi task numpuk di Redis (`celery` queue 1304 → 1390 entries: health_check + scan_watchdog + auto_comment_watchdog). Worker ALIVE tapi DEADLOCK = defense gap. **Phase M-1** TDD strict: `@app.task(time_limit=900, soft_time_limit=720)` di scan + `(600, 480)` di auto_comment + import `SoftTimeLimitExceeded` from `celery.exceptions` + explicit handler sebelum generic `except Exception`. scan finalize dengan `aborted_reason='soft_time_limit'`+`error_message='celery soft_time_limit exceeded'`+resched chain; auto_comment return `{action:'soft_timeout', reason:'soft_time_limit'}` (finally-block tetep resched). 9 test (3 decorator scan + 3 decorator comment + 3 handler). **Phase M-2** `bot/celery_app.py` +`_scan_outer_timeout()` (env `SCAN_OUTER_TIMEOUT_SECONDS` default 720s) +`_comment_outer_timeout()` (env `COMMENT_OUTER_TIMEOUT_SECONDS` default 420s). `bot/tasks.py _run_scan_all_sources` line 614 wrap `asyncio.run(asyncio.wait_for(_scan_enabled_sources(...), timeout=_scan_outer_timeout()))`; `auto_comment_next` line 1360 wrap `asyncio.run(asyncio.wait_for(send_comment(...), timeout=_comment_outer_timeout()))`. Handler `except asyncio.TimeoutError` sebelum `except SoftTimeLimitExceeded`: scan finalize `aborted_reason='asyncio_timeout'`+resched, comment return `{action:'async_timeout', reason:'asyncio_timeout'}`. 6 test (2 knob default + 2 knob env override + 2 handler). **Defense ladder**: outer asyncio timeout (720/420s) → soft_time_limit (720/480s) → hard time_limit (900/600s SIGKILL). Inner fire dulu = graceful unwind, hard timeout = last-resort. Full suite **825 passed**. **Recovery**: stop fb-bot-worker (`systemctl stop`) → `pkill -9 headless_shell` + `pkill -9 playwright/driver/node` (kill orphan Chromium PID 199078+1894317) → `redis-cli -p 6382 DEL celery` (1390→0) → reset `ScannerRun id=493` ke `failed+aborted='manual_recovery_phase_m'+error_message='stuck >36h, recovered before Phase M-1+M-2 deploy'+finished_at=now()` via PYTHONPATH script → `systemctl start fb-bot-worker`. **Deploy verify** 12:45 UTC: 3 service active, SHA `5e601d5`, runtime introspection scan/comment time_limit + outer timeout match expected, smoke `POST /api/v1/scanner/run-now` task `ff8f2593` ScannerRun id=494 SUCCESS in 81.3s (jitter 48.4s + scan 33s) ins=0/upd=0/skip=0 (no fresh post). auto_comment_watchdog kick chain (idle 132517s = 36.8h ke depan threshold), CommentHistory id=384 DRAFT post=347 (dry-run masih aktif), self-resched chain `9fc79cf2`+`1f931694` di-receive di queue. Redis backlog 0. FB account id=1 ACTIVE failure_count=0. **Cookie observation #6**: baseline 2026-05-14 23:40 UTC, sekarang ~85h cookie masih ACTIVE. Pipeline freeze 2026-05-16/17 BUKAN cookie burn (root cause Playwright pipe deadlock di asyncio loop) — Phase I cookie hardening **likely fixed**, pending re-validation 1-2 minggu post-M. HEAD `5e601d5`. Plan `phase-m-worker-deadlock-defense.md`. M-3..M-6 pending. |
| 2026-05-15 | phase K dry-run + phase L (1/2/3) deploy live + obs#6 baseline | **Phase K dry-run** ship via TDD (K-1 AutoCommentService → K-2 cadence env → K-3 auto_comment_next + finally-resched → K-4 watchdog → K-5 dry-run mode). `bot/celery_app.py` +`_auto_comment_min/max/watchdog_interval()` (env defaults 720/1800/300s) + `_auto_comment_dry_run()` (env `AUTO_COMMENT_DRY_RUN=1`). `bot/tasks.py` +`_enqueue_next_comment()` random countdown helper, +`auto_comment_next` lifecycle (kill-switch → pick eligible → rate-limit pre-check → ACTIVE account → AI draft → DRY-RUN branch (skip send, log status='DRAFT') OR Playwright send → SENT/FAILED with finally-resched), +`auto_comment_watchdog` stale-chain detection mirror Phase J. `server/services/auto_comment_service.py` `pick_next_eligible_post()` (TrendingPost.status='NEW' AND no CommentHistory row, FIFO oldest-first). 47 K-tests + 14 K-5 dry-run tests = 61. **Phase L bug-fix series after obs#6 cookie burn investigation:** **L-1** `bot/tasks.py scan_all_sources` entry guard — query in-flight ScannerRun (status='running' AND started_at within `SCAN_INFLIGHT_WINDOW_SECONDS` default 600s), if found return `{aborted:True, reason:'duplicate_chain'}` no row insert no resched (absorbs orphan chain races). 7 tests. **L-2** `scan_watchdog` zombie reaper — query `status='running' AND started_at < now - SCAN_RUNNING_TIMEOUT_SECONDS` (default 600s), flip to failed + `aborted_reason='watchdog_zombie_reap'` + auto-kick fresh scan. Catches worker crash mid-scan that would otherwise freeze L-1 guard forever. 6 tests. **L-3** `auto_comment_next` Redis SET NX EX fire-lock `auto_comment:fire_lock` TTL=`_auto_comment_min_interval()` — at task start before any work, atomic acquire; if held → skip + NO resched (kills broker-backlog explosion observed at 91 ticks/30 min after restart). Fail-open on Redis error. 8 tests. K-3+K-5 tests updated to bypass real Redis lock via monkeypatch. Full suite **832 passed** (+57 since J baseline 775). **Depl... [truncated]
| 2026-05-14 | phase I-C followup: manual noVNC login + Playwright Chromium → BREAKTHROUGH | **Path Manual Login** (after I-C auto-login dead-end karena Arkose dari IP VPS hard-flagged). **Setup remote desktop:** server `rdpkhorur` udah punya 4-stack noVNC pattern di display `:1`/`:2` (existing), gue spawn display `:3` baru: `Xvfb :3` (1920x1080x24) → `openbox` → `x11vnc` port `5903` → `websockify` port `6083` bind to Tailscale IP `100.85.175.66`, password `WQc0M0zzrqDu` di `/etc/novnc/passwd-3`, 4 systemd unit `headless-{xvfb,openbox,x11vnc,novnc}-3.service` enabled+started. **Tunnel SSH** dari laptop ke server (akun Tailscale beda jadi bypass via `ssh -fN -L 6083:100.85.175.66:6083 rdpkhorur`), buka `http://localhost:6083/vnc.html` di Brave laptop. **Snap Chromium broken di display :3** (`/user.slice/user-1000.slice/session-N.scope is not a snap cgroup` — silent fail launch karena confinement issue). **Switch ke Playwright Chromium** dari `~/.cache/ms-playwright/chromium-1148/chrome-linux/chrome` — binary identik dengan yang scanner pake, profile dir compatible 100%. Profile dir bikin ulang `mkdir -p /home/ubuntu/.fb-bot/fb-profiles/account-1` (sebelumnya keapus full saat I-C wipe test, snap chromium ga create dir kalau parent ga ada). User login normal di Brave noVNC → cookies tersimpan 23:40 UTC, profile size 58M, no `Singleton*` lock files (Chromium close clean). **Smoke verify**: enable source id=1 (home_feed) only, keep id=2 (group flagged) disabled, login admin via `/api/v1/auth/login` → trigger `POST /api/v1/scanner/run-now` (token Bearer auth, prefix `/api/v1` mandatory). **ScannerRun id=312 result: status=success, started 23:41:55 → finished 23:45:38 (3m43s, jitter 101s + scan 122s), enabled_sources=1, successful_scans=1, scan_errors=0, inserted=3, skipped=8 (dedup kerja), aborted_reason=NULL**. fb_account id=1 status=ACTIVE, failure_count=0 (no flip). Worker log clean, no checkpoint/login-wall signals. **Observation window ke-4 baseline T0=2026-05-14 23:40 UTC** (cookie save time). Target survival `>5h` baseline (1st obs) untuk confirmed breakthrough. Variabel terisolasi: persistent profile fresh + manual login + UA Chrome 147 + group flagged dropped — kalau awet >5h, root cause confirmed = headless Playwright login fingerprint (bukan IP/akun). Sources state: id=1 enabled=1, id=2 enabled=0. **Side fix:** Hermes Agent vision tool broken karena 9router proxy strip `image_url` dari payload (Bug 1 capability filter, Bug 2 Anthropic format adapter `[image omitted]`), plan `~/.hermes/plans/fix-9router-vision-strip.md` self-contained handoff buat Codex CLI patch — applied OK, vision_analyze functional kembali. |
| 2026-05-14 | phase I-C persistent browser profile DEPLOYED — outcome FAIL | **Phase I-C** ship complete via TDD strict (RED→GREEN per sub-task, 11 atomic commits). I-C-1 `bot/modules/browser_profile.py` `get_profile_root()` (env `FB_PROFILE_ROOT` default `~/.fb-bot/fb-profiles`) + `get_profile_path(account_id)` + `wipe_profile(account_id)` (6 test). I-C-2 `bot/modules/fb_session.py` +`create_persistent_session(playwright, account_id, *, cookies, user_agent, viewport, locale, timezone_id)` co-existing dengan `create_session_context` (NOT replace, env-flag routing rollback-friendly). First-run cookie bootstrap via `(profile_path/"Default").exists()` check — first run `add_cookies` from upload, subsequent skip (Chromium auto-restore from profile cookie store). Stealth init script attached pre-navigation (6 test). I-C-3 routing wire: `bot/modules/source_collector.py` + `bot/modules/comment_sender.py` signature add `account_id: int | None = None`, dual-path branch via `os.getenv("FB_USE_PERSISTENT_PROFILE") == "1" and account_id`, finally cleanup branch on `_persistent`/`browser` (3+3 test). Plumbing: `bot/tasks._scan_enabled_sources(..., account_id=None)` pass via `scan_kwargs`, `_run_scan_all_sources` passes `account_id=account.id`; `server/routers/trending.py POST /comment` passes `account_id=account.id`. I-C-4 `server/routers/fb_accounts.py` import `wipe_profile`, hook ke DELETE endpoint. I-C-5 same router hook ke re-upload-cookie endpoint (cookie taint = profile taint, wipe before replace). 3 test cleanup. **Full suite 715 passed (+21 sejak f9d01ee)**. **Deploy event 2026-05-14 ~08:50 UTC:** push 11 commits `7477c52..795f357`, server `git pull` initially blocked (scp drift selama development) → `git reset --hard origin/main` (.env confirmed gitignored, safe). Append `FB_USE_PERSISTENT_PROFILE=1` ke `.env`, `sudo systemctl restart fb-bot-api fb-bot-worker fb-bot-beat` all active. **Smoke trigger 08:51:39 UTC:** ScannerRun id=280 finished 08:54:49 UTC `aborted_reason=cookie_expired`. Source 1 sukses, source 2 (group `2160701604115230`) trigger login wall — **pattern identik 1st observation**. **Profile dir FUNCTIONAL ✓**: `~/.fb-bot/fb-profiles/account-1/Default/` tercipta 08:53 UTC dengan Cookies, IndexedDB, Local/Session Storage, GPUCache (bootstrap path executed, persistent context launching properly). **Outcome ❌**: survival window ~1.5h dari Tstart 2nd 07:24 UTC, **2.5x lebih cepet** dari baseline 1st 5h. **Verdict:** flag bukan di session/profile layer. Hipotesis: group-specific watchlist atau akun Digi Markt pre-flagged. Phase I closed — next lever proxy rotation / drop flagged group / ganti akun, pending bos approval. HEAD `795f357`. |
| 2026-05-14 | comment activity widget + quota gate bypass deploy live | **Comment Activity Widget** (Layer 2 UX swap, plan `comment-activity-widget.md`). Branch `feat/comment-activity-widget` 7 commit merged → `main` `f9d01ee`. **Backend:** `RateLimitService._max_per_window()` env-override `MAX_COMMENTS_PER_WINDOW` (default 5, runtime read tiap call, invalid int → fallback + warn) — service intact, rollback-friendly. New `server/services/comment_activity_service.py` `CommentActivityService.today_count()` + `today_snapshot()` query `CommentHistory.status='SENT'` di window `[start_utc, end_utc)` derived from `ZoneInfo("Asia/Jakarta")` (UTC+7 no DST round-trip). New router `GET /api/v1/comment-activity/today` any-auth → `{count_today, date, tz}`. **Frontend:** `dashboard/src/components/comment-activity-widget.tsx` polling 30s, query key `comment-activity-today`, header swap `QuotaWidget`→`CommentActivityWidget` (old `quota-widget.tsx` deleted). Trending page buang `quotaQuery` + `Gauge` icon + Quota Card banner; `sendDisabled` jadi `!isAdmin` only; `sendComment` invalidate `comment-activity-today` (ganti `rate-limit-status`). 17 new test (rate-limit env 4 + service 9 + router 4). **Deploy:** server pull `f9d01ee`, append `MAX_COMMENTS_PER_WINDOW=9999` ke `.env`, restart 3 services all active, smoke `/rate-limit/status` → `limit=9999, remaining=9999, allowed=true` + `/comment-activity/today` → `{count_today:0, date:"2026-05-14", tz:"Asia/Jakarta"}` ✓. Bundle grep `app-header` + `Trending` punya `Komen hari ini` + `comment-activity-today` refs, old `Quota komen` GONE. Full suite **694 passed** (+17). HEAD `f9d01ee`. Plan `comment-activity-widget.md` semua task ✅. **Side observation:** Phase I observation window FAIL — akun id=1 flip EXPIRED ~T+5h post re-upload (login wall facebook.com/groups/...), trigger I-C unblock per rollout plan. |
| 2026-05-13 | phase I-E stealth init script deploy live | **Phase I-E** (of Phase I Session Hardening, final cheap stealth fix). `bot/modules/fb_session.py` +`STEALTH_INIT_SCRIPT` constant patch 4 headless markers lewat `Object.defineProperty`: `navigator.webdriver` → `false` (Playwright default `true`, real Chrome always `false`), `navigator.plugins` → 3-entry array (`Chrome PDF Plugin`/`Chrome PDF Viewer`/`Native Client` — headless default `length=0`), `navigator.languages` → `['id-ID','id','en-US','en']` (match context `locale=id-ID` biar ga mismatch), `window.chrome` → minimal `{runtime:{}}` shim (undefined under headless). `create_session_context` attach via `await context.add_init_script(STEALTH_INIT_SCRIPT)` RIGHT BEFORE `add_cookies` — before first navigation, so every page FB loads in this context evaluates patched navigator from doc-start. Skip full `playwright-stealth` dep per plan §1 (YAGNI, parked §6). 6 new test: RED `ed0933e` (stealth constant present + add_init_script wired), GREEN `2a00bc7` (impl), fixture backfill `948858c` (update existing `create_session_context` tests dengan AsyncMock add_init_script) + `f3d2113` (sender/collector context fixtures). Full suite **677 passed** (+6 sejak I-D). Deploy: server already on `f3d2113`, all 3 services (`fb-bot-api`/`-worker`/`-beat`) active, smoke grep 4 stealth refs di `bot/modules/fb_session.py` ✓. Phase I tasks done semua except I-C (parked, tunggu observation). HEAD `f3d2113`. |
| 2026-05-13 | phase I-D scanner cadence humanization deploy live | **Phase I-D** (of Phase I Session Hardening). `bot/celery_app.py _scan_interval()` default bumped **900→1800** (15→30 min) — FB anti-bot less likely flag rapid on-the-second auth rhythm from single VPS IP. `bot/tasks.py` +2 module-level coroutines: `_sleep_startup_jitter()` (0-120s random, fired once at start of every scan cycle biar beat ticks spread across wall-clock) + `_sleep_inter_source()` (30-90s random think-time, inserted BETWEEN sources within one cycle — mimics human idling between feeds). `_scan_enabled_sources` wires jitter at loop entry + think-time guard `if idx > 0`. 5 new test (test_celery_schedule 3x: default=1800, env override respected, >=1500 guardrail + test_scan_all_sources 2x: jitter fires once & before first scan, think-time fires between 2 sources only). Autouse fixture `_no_scanner_sleep` di `tests/test_scan_all_sources.py` no-op cadence sleeps — existing tests tetap 7s bukan 16min. Env override `SCAN_INTERVAL_SECONDS` masih respected. Full suite **671 passed** (+5 sejak I-B). Deploy: `sudo systemctl restart fb-bot-api fb-bot-worker fb-bot-beat` all active, beat reloaded clean 04:06:11 UTC, smoke via Python REPL verify `_scan_interval()==1800`, `_STARTUP_JITTER_MAX_SECONDS=120`, `_INTER_SOURCE_DELAY=30-90`. Commits: RED `9a1983f` → GREEN `3777354` → test-speedup `1db3561`. HEAD `1db3561`. |
| 2026-05-13 | phase I-B cookie rotation capture deploy live | **Phase I-B** (of Phase I Session Hardening). I-B-1 `bot/modules/fb_session.py` `capture_cookies_from_context(context, domain_suffix='facebook.com')` helper — filter by domain ending (catch `.facebook.com` + `m.facebook.com` + `www.facebook.com`), tolerant ke None/empty return, 5 test (`240e7da`). I-B-2 `FBAccountService.refresh_cookies_silent(account_id, cookies=...)` — overwrite only `cookies_encrypted`, NO touch status/profile/failure_count, reject empty dict + missing `c_user`, never raise on missing id, 6 test w/ paired stub fixture for `encrypt_cookies`/`decrypt_cookies` (`86ec6b8`). I-B-3 wire: `scan_source` + `send_comment` signature add `on_cookies_refresh: Callable[[dict], Awaitable[None]] \| None`, harvest via `capture_cookies_from_context` right before context close, swallow callback exc (best-effort), `_run_scan_all_sources` builds callback via `_make_cookie_refresh_callback(db, account_id)`, trending `POST /comment` router inline closure, 4 test (3 scanner + 1 orchestrator) (`8b36028`). Full suite **666 passed** (+15 since I-A). Deploy: `sudo systemctl restart fb-bot-api fb-bot-worker fb-bot-beat` active, smoke via SessionLocal: `refresh_cookies_silent(1, {xs:'ROTATED_BY_SMOKE'})` → DB `xs` updated, `status=EXPIRED` unchanged, restore original cookies clean ✓. Phase I-C (persistent browser profile) next — observe 24h per rollout plan §5. HEAD `8b36028`. |


## Dashboard & API Access (SSH Tunnel)

Local gak jalanin runtime. FastAPI (uvicorn `:8100`) serve static `dashboard/dist` langsung + API di path yang sama — jadi satu tunnel cukup. Alias di `~/.bashrc`:

| Alias | Fungsi | URL lokal |
|-------|--------|-----------|
| `fbtun` | Tunnel dashboard + API (background, `-fN`) | http://localhost:8100 |
| `fbtun-fg` | Same tunnel, foreground (Ctrl+C to kill) | http://localhost:8100 |
| `fbtun-status` | Cek tunnel background yang lagi jalan | — |
| `fbtun-kill` | Kill tunnel background | — |
| `fb-ssh` | SSH interactive ke server | — |

Raw command (kalau alias belum ke-load):

```
ssh -fN -L 8100:127.0.0.1:8100 rdpkhorur
```

Server listen: uvicorn `:8100` mount `/api/v1/*` (backend) + `/assets/*` (static) + SPA fallback ke `index.html`. Gak ada nginx, gak ada reverse proxy — FastAPI solo.

## Git Conventions

- Branch: `feat/...`, `fix/...`, atau relay branch `agent-relay-<short-hash>`.
- Commit message: lowercase, prefix + colon + space + deskripsi singkat.
  - `feat: add sosmed bundle builder`
  - `fix: refund failed sosmed wallet orders`
  - `refactor: normalize jap service codes`
  - `chore: update deploy script`
- Jangan amend commit yang sudah di-push kecuali diminta eksplisit.
- Prefer staging specific files over `git add .`.

## Error Handling & Recovery

- Kalau approach gagal 2x, stop dan diagnosa root cause. Jangan patch incremental tanpa paham masalahnya.
- Partial failure di multi-step operation: kasih recovery-friendly message, reload state, biarkan user/admin lanjut manual dari step yang gagal.
- Log/event setiap state transition buat audit trail.
