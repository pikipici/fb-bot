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

**Last shipped:** Phase I-C persistent browser profile deployed live
(2026-05-14, HEAD `795f357`). Profile dir functional, **outcome FAIL**: akun
flip EXPIRED ~T+1.5h (lebih cepet dari baseline 5h). Full suite 715 pass.

**Phase I progress:**
- ✅ I-A Per-account fingerprint pinning
- ✅ I-B Cookie rotation capture
- ✅ I-C Persistent browser profile — **DEPLOYED**, outcome FAIL (flag lebih dalam)
- ✅ I-D Scanner cadence (interval + jitter + think-time)
- ✅ I-E Stealth patch (`navigator.webdriver=false` + plugins/languages/window.chrome)

**Phase I observation post-I-C — VERDICT: stealth/profile ga cukup.**

- **Tstart 2nd:** 2026-05-14 07:24 UTC (cookie re-upload).
- **Deploy I-C:** 2026-05-14 ~08:50 UTC.
- **Tflip 2nd:** 2026-05-14 08:54:49 UTC (`cookie_expired`, group
  `2160701604115230` login wall — pattern identik dengan 1st observation).
- **Survival window 2nd:** ~1.5h (2.5x lebih cepet dari baseline 5h).
- **Persistent profile FUNCTIONAL** (`~/.fb-bot/fb-profiles/account-1/Default/`
  tercipta dengan Cookies/IndexedDB/Storage), tapi flag layer lebih dalam dari
  yang bisa di-mitigate stealth.
- **Hipotesis baru:** group-specific watchlist (FB internal flag pada group
  ID `2160701604115230`) atau akun Digi Markt pre-flagged dari historical
  activity. Cookie + IP + akun trio yang kena flag, persistent profile cuma
  nambah fingerprint stability — bukan layer yang relevant.

**Next step (chosen):**
- Phase I closed. Next lever bukan stealth lagi:
  - **Option A:** Proxy/IP rotation (residential proxy per akun) — Phase J?
  - **Option B:** Drop group `2160701604115230` dari source list, observe
    apakah pages-only sources lebih awet.
  - **Option C:** Ganti akun (Digi Markt mungkin terminal-flagged).
- Decision pending bos approval — gak ada code change sampai pilih lever.

## Activity Log

| Tanggal | Status | Summary |
|---------|--------|---------|
| 2026-05-14 | phase I-C followup: manual noVNC login + Playwright Chromium → BREAKTHROUGH | **Path Manual Login** (after I-C auto-login dead-end karena Arkose dari IP VPS hard-flagged). **Setup remote desktop:** server `rdpkhorur` udah punya 4-stack noVNC pattern di display `:1`/`:2` (existing), gue spawn display `:3` baru: `Xvfb :3` (1920x1080x24) → `openbox` → `x11vnc` port `5903` → `websockify` port `6083` bind to Tailscale IP `100.85.175.66`, password `WQc0M0zzrqDu` di `/etc/novnc/passwd-3`, 4 systemd unit `headless-{xvfb,openbox,x11vnc,novnc}-3.service` enabled+started. **Tunnel SSH** dari laptop ke server (akun Tailscale beda jadi bypass via `ssh -fN -L 6083:100.85.175.66:6083 rdpkhorur`), buka `http://localhost:6083/vnc.html` di Brave laptop. **Snap Chromium broken di display :3** (`/user.slice/user-1000.slice/session-N.scope is not a snap cgroup` — silent fail launch karena confinement issue). **Switch ke Playwright Chromium** dari `~/.cache/ms-playwright/chromium-1148/chrome-linux/chrome` — binary identik dengan yang scanner pake, profile dir compatible 100%. Profile dir bikin ulang `mkdir -p /home/ubuntu/.fb-bot/fb-profiles/account-1` (sebelumnya keapus full saat I-C wipe test, snap chromium ga create dir kalau parent ga ada). User login normal di Brave noVNC → cookies tersimpan 23:40 UTC, profile size 58M, no `Singleton*` lock files (Chromium close clean). **Smoke verify**: enable source id=1 (home_feed) only, keep id=2 (group flagged) disabled, login admin via `/api/v1/auth/login` → trigger `POST /api/v1/scanner/run-now` (token Bearer auth, prefix `/api/v1` mandatory). **ScannerRun id=312 result: status=success, started 23:41:55 → finished 23:45:38 (3m43s, jitter 101s + scan 122s), enabled_sources=1, successful_scans=1, scan_errors=0, inserted=3, skipped=8 (dedup kerja), aborted_reason=NULL**. fb_account id=1 status=ACTIVE, failure_count=0 (no flip). Worker log clean, no checkpoint/login-wall signals. **Observation window ke-4 baseline T0=2026-05-14 23:40 UTC** (cookie save time). Target survival `>5h` baseline (1st obs) untuk confirmed breakthrough. Variabel terisolasi: persistent profile fresh + manual login + UA Chrome 147 + group flagged dropped — kalau awet >5h, root cause confirmed = headless Playwright login fingerprint (bukan IP/akun). Sources state: id=1 enabled=1, id=2 enabled=0. **Side fix:** Hermes Agent vision tool broken karena 9router proxy strip `image_url` dari payload (Bug 1 capability filter, Bug 2 Anthropic format adapter `[image omitted]`), plan `~/.hermes/plans/fix-9router-vision-strip.md` self-contained handoff buat Codex CLI patch — applied OK, vision_analyze functional kembali. |
| 2026-05-14 | phase I-C persistent browser profile DEPLOYED — outcome FAIL | **Phase I-C** ship complete via TDD strict (RED→GREEN per sub-task, 11 atomic commits). I-C-1 `bot/modules/browser_profile.py` `get_profile_root()` (env `FB_PROFILE_ROOT` default `~/.fb-bot/fb-profiles`) + `get_profile_path(account_id)` + `wipe_profile(account_id)` (6 test). I-C-2 `bot/modules/fb_session.py` +`create_persistent_session(playwright, account_id, *, cookies, user_agent, viewport, locale, timezone_id)` co-existing dengan `create_session_context` (NOT replace, env-flag routing rollback-friendly). First-run cookie bootstrap via `(profile_path/"Default").exists()` check — first run `add_cookies` from upload, subsequent skip (Chromium auto-restore from profile cookie store). Stealth init script attached pre-navigation (6 test). I-C-3 routing wire: `bot/modules/source_collector.py` + `bot/modules/comment_sender.py` signature add `account_id: int | None = None`, dual-path branch via `os.getenv("FB_USE_PERSISTENT_PROFILE") == "1" and account_id`, finally cleanup branch on `_persistent`/`browser` (3+3 test). Plumbing: `bot/tasks._scan_enabled_sources(..., account_id=None)` pass via `scan_kwargs`, `_run_scan_all_sources` passes `account_id=account.id`; `server/routers/trending.py POST /comment` passes `account_id=account.id`. I-C-4 `server/routers/fb_accounts.py` import `wipe_profile`, hook ke DELETE endpoint. I-C-5 same router hook ke re-upload-cookie endpoint (cookie taint = profile taint, wipe before replace). 3 test cleanup. **Full suite 715 passed (+21 sejak f9d01ee)**. **Deploy event 2026-05-14 ~08:50 UTC:** push 11 commits `7477c52..795f357`, server `git pull` initially blocked (scp drift selama development) → `git reset --hard origin/main` (.env confirmed gitignored, safe). Append `FB_USE_PERSISTENT_PROFILE=1` ke `.env`, `sudo systemctl restart fb-bot-api fb-bot-worker fb-bot-beat` all active. **Smoke trigger 08:51:39 UTC:** ScannerRun id=280 finished 08:54:49 UTC `aborted_reason=cookie_expired`. Source 1 sukses, source 2 (group `2160701604115230`) trigger login wall — **pattern identik 1st observation**. **Profile dir FUNCTIONAL ✓**: `~/.fb-bot/fb-profiles/account-1/Default/` tercipta 08:53 UTC dengan Cookies, IndexedDB, Local/Session Storage, GPUCache (bootstrap path executed, persistent context launching properly). **Outcome ❌**: survival window ~1.5h dari Tstart 2nd 07:24 UTC, **2.5x lebih cepet** dari baseline 1st 5h. **Verdict:** flag bukan di session/profile layer. Hipotesis: group-specific watchlist atau akun Digi Markt pre-flagged. Phase I closed — next lever proxy rotation / drop flagged group / ganti akun, pending bos approval. HEAD `795f357`. |
| 2026-05-14 | comment activity widget + quota gate bypass deploy live | **Comment Activity Widget** (Layer 2 UX swap, plan `comment-activity-widget.md`). Branch `feat/comment-activity-widget` 7 commit merged → `main` `f9d01ee`. **Backend:** `RateLimitService._max_per_window()` env-override `MAX_COMMENTS_PER_WINDOW` (default 5, runtime read tiap call, invalid int → fallback + warn) — service intact, rollback-friendly. New `server/services/comment_activity_service.py` `CommentActivityService.today_count()` + `today_snapshot()` query `CommentHistory.status='SENT'` di window `[start_utc, end_utc)` derived from `ZoneInfo("Asia/Jakarta")` (UTC+7 no DST round-trip). New router `GET /api/v1/comment-activity/today` any-auth → `{count_today, date, tz}`. **Frontend:** `dashboard/src/components/comment-activity-widget.tsx` polling 30s, query key `comment-activity-today`, header swap `QuotaWidget`→`CommentActivityWidget` (old `quota-widget.tsx` deleted). Trending page buang `quotaQuery` + `Gauge` icon + Quota Card banner; `sendDisabled` jadi `!isAdmin` only; `sendComment` invalidate `comment-activity-today` (ganti `rate-limit-status`). 17 new test (rate-limit env 4 + service 9 + router 4). **Deploy:** server pull `f9d01ee`, append `MAX_COMMENTS_PER_WINDOW=9999` ke `.env`, restart 3 services all active, smoke `/rate-limit/status` → `limit=9999, remaining=9999, allowed=true` + `/comment-activity/today` → `{count_today:0, date:"2026-05-14", tz:"Asia/Jakarta"}` ✓. Bundle grep `app-header` + `Trending` punya `Komen hari ini` + `comment-activity-today` refs, old `Quota komen` GONE. Full suite **694 passed** (+17). HEAD `f9d01ee`. Plan `comment-activity-widget.md` semua task ✅. **Side observation:** Phase I observation window FAIL — akun id=1 flip EXPIRED ~T+5h post re-upload (login wall facebook.com/groups/...), trigger I-C unblock per rollout plan. |
| 2026-05-13 | phase I-E stealth init script deploy live | **Phase I-E** (of Phase I Session Hardening, final cheap stealth fix). `bot/modules/fb_session.py` +`STEALTH_INIT_SCRIPT` constant patch 4 headless markers lewat `Object.defineProperty`: `navigator.webdriver` → `false` (Playwright default `true`, real Chrome always `false`), `navigator.plugins` → 3-entry array (`Chrome PDF Plugin`/`Chrome PDF Viewer`/`Native Client` — headless default `length=0`), `navigator.languages` → `['id-ID','id','en-US','en']` (match context `locale=id-ID` biar ga mismatch), `window.chrome` → minimal `{runtime:{}}` shim (undefined under headless). `create_session_context` attach via `await context.add_init_script(STEALTH_INIT_SCRIPT)` RIGHT BEFORE `add_cookies` — before first navigation, so every page FB loads in this context evaluates patched navigator from doc-start. Skip full `playwright-stealth` dep per plan §1 (YAGNI, parked §6). 6 new test: RED `ed0933e` (stealth constant present + add_init_script wired), GREEN `2a00bc7` (impl), fixture backfill `948858c` (update existing `create_session_context` tests dengan AsyncMock add_init_script) + `f3d2113` (sender/collector context fixtures). Full suite **677 passed** (+6 sejak I-D). Deploy: server already on `f3d2113`, all 3 services (`fb-bot-api`/`-worker`/`-beat`) active, smoke grep 4 stealth refs di `bot/modules/fb_session.py` ✓. Phase I tasks done semua except I-C (parked, tunggu observation). HEAD `f3d2113`. |
| 2026-05-13 | phase I-D scanner cadence humanization deploy live | **Phase I-D** (of Phase I Session Hardening). `bot/celery_app.py _scan_interval()` default bumped **900→1800** (15→30 min) — FB anti-bot less likely flag rapid on-the-second auth rhythm from single VPS IP. `bot/tasks.py` +2 module-level coroutines: `_sleep_startup_jitter()` (0-120s random, fired once at start of every scan cycle biar beat ticks spread across wall-clock) + `_sleep_inter_source()` (30-90s random think-time, inserted BETWEEN sources within one cycle — mimics human idling between feeds). `_scan_enabled_sources` wires jitter at loop entry + think-time guard `if idx > 0`. 5 new test (test_celery_schedule 3x: default=1800, env override respected, >=1500 guardrail + test_scan_all_sources 2x: jitter fires once & before first scan, think-time fires between 2 sources only). Autouse fixture `_no_scanner_sleep` di `tests/test_scan_all_sources.py` no-op cadence sleeps — existing tests tetap 7s bukan 16min. Env override `SCAN_INTERVAL_SECONDS` masih respected. Full suite **671 passed** (+5 sejak I-B). Deploy: `sudo systemctl restart fb-bot-api fb-bot-worker fb-bot-beat` all active, beat reloaded clean 04:06:11 UTC, smoke via Python REPL verify `_scan_interval()==1800`, `_STARTUP_JITTER_MAX_SECONDS=120`, `_INTER_SOURCE_DELAY=30-90`. Commits: RED `9a1983f` → GREEN `3777354` → test-speedup `1db3561`. HEAD `1db3561`. |
| 2026-05-13 | phase I-B cookie rotation capture deploy live | **Phase I-B** (of Phase I Session Hardening). I-B-1 `bot/modules/fb_session.py` `capture_cookies_from_context(context, domain_suffix='facebook.com')` helper — filter by domain ending (catch `.facebook.com` + `m.facebook.com` + `www.facebook.com`), tolerant ke None/empty return, 5 test (`240e7da`). I-B-2 `FBAccountService.refresh_cookies_silent(account_id, cookies=...)` — overwrite only `cookies_encrypted`, NO touch status/profile/failure_count, reject empty dict + missing `c_user`, never raise on missing id, 6 test w/ paired stub fixture for `encrypt_cookies`/`decrypt_cookies` (`86ec6b8`). I-B-3 wire: `scan_source` + `send_comment` signature add `on_cookies_refresh: Callable[[dict], Awaitable[None]] \| None`, harvest via `capture_cookies_from_context` right before context close, swallow callback exc (best-effort), `_run_scan_all_sources` builds callback via `_make_cookie_refresh_callback(db, account_id)`, trending `POST /comment` router inline closure, 4 test (3 scanner + 1 orchestrator) (`8b36028`). Full suite **666 passed** (+15 since I-A). Deploy: `sudo systemctl restart fb-bot-api fb-bot-worker fb-bot-beat` active, smoke via SessionLocal: `refresh_cookies_silent(1, {xs:'ROTATED_BY_SMOKE'})` → DB `xs` updated, `status=EXPIRED` unchanged, restore original cookies clean ✓. Phase I-C (persistent browser profile) next — observe 24h per rollout plan §5. HEAD `8b36028`. |
| 2026-05-13 | phase I-A session hardening deploy live | **Phase I-A Per-Account Fingerprint Pinning** (of Phase I Session Hardening, trigger: user lapor cookie cepat expired). I-A-1 alembic rev `005_fb_fingerprint` + `FBAccount.browser_ua`/`viewport_w`/`viewport_h` columns (nullable, lazy-assigned) (`e06186f`). I-A-2 `bot/modules/fingerprint_pool.py` pool of 3 Chrome 131/132 UA + 5 desktop viewports + `FBAccountService.ensure_fingerprint(account_id)` idempotent helper (partial-null safe, auto-persist), 4 test (`622fa2d`). I-A-3 wire pinned UA+viewport: `scan_source` + `send_comment` signatures add `viewport=None`, `_run_scan_all_sources` + trending `POST /comment` router call `ensure_fingerprint(account.id)` sebelum invoke, `DEFAULT_USER_AGENT` upgrade Chrome 120→131, 2 test (source_collector forward kwargs + orchestrator pin per-account) (`d66f604`). Full suite **651 passed** (+13 since F7). Deploy: `alembic upgrade head` 004→005 clean, `sudo systemctl restart fb-bot-api fb-bot-worker fb-bot-beat` all active, smoke via SessionLocal: `ensure_fingerprint(1)` assigned UA Chrome 132 + viewport 1536x864, idempotent check (ua,w,h) tuple stable, DB row verified populated. Phase I-B (cookie rotation capture) + I-C (persistent profile) pending next — observe 24h per rollout plan §5. Roadmap: `.kiro/steering/phase-i-session-hardening.md`. HEAD `ac4f971`. |
| 2026-05-12 | post-F5 polish rollup deploy live | **F6** `GET /api/v1/history` list endpoint (status filter SENT/FAILED/PENDING, pagination `limit`/`offset`, post summary embed) + `/history` page w/ 25/page pagination (`d82ed4d`..`316cf17`). **Phase G hardening**: app-wide ErrorBoundary + global React Query error toast via queryCache/mutationCache.onError (`e653106`), rewrite README+add docs/ARCHITECTURE.md+docs/DEPLOY.md (`a3e8459`), route-level `lazy(() => import)` code-split + global QuotaWidget di header + dark-mode default, cleanup obsolete review-queue page + drafts/approvals routers (`9a7d808`..`f94333d`). **AI draft** `AIDraftService` OpenAI-compat sumopod.com (default MiniMax-M2.7-highspeed) + `POST /trending/{id}/ai-draft` admin-only dengan 15s per-user in-memory cooldown + UI sparkle wand button w/ dirty-check (`e33a1ac`..`d7e2ca7`). **Scanner audit** `ScannerRun` table + `GET /scanner/status` + `POST /scanner/run-now` + `ScannerIndicator` component (dot freshness) + admin Scan-now button + UTC-explicit ISO serialization fix (7-jam tz bug) (`ddb35d2`..`e2ba6c8`). **Cookie health** `bot/modules/fb_auth_probe.py` DOM login-wall probe, scanner + sender `CookieExpiredError` walaupun URL terlihat normal, global `AccountStatusBanner` surface EXPIRED/BLOCKED (`f2d69a9`..`2f31152`). **Photo viewer composer** scroll-to-bottom hydration + EN/ID multi-locale (`Berkomentar`/`Tulis komentar`) + text-based stub fallback (`ac488ea`..`c107c1a`). **Trending UX** reject stories/reel/watch URL 415 + shared `fb_url` util + badge "Belum komen" + Fresh badge via `localStorage.lastSeenAt` (`7bcaf6d`..`029144e`). HEAD `029144e`. |

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
