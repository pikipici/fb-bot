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

## Activity Log

| Tanggal | Status | Summary |
|---------|--------|---------|
| 2026-05-13 | phase I-A session hardening deploy live | **Phase I-A Per-Account Fingerprint Pinning** (of Phase I Session Hardening, trigger: user lapor cookie cepat expired). I-A-1 alembic rev `005_fb_fingerprint` + `FBAccount.browser_ua`/`viewport_w`/`viewport_h` columns (nullable, lazy-assigned) (`e06186f`). I-A-2 `bot/modules/fingerprint_pool.py` pool of 3 Chrome 131/132 UA + 5 desktop viewports + `FBAccountService.ensure_fingerprint(account_id)` idempotent helper (partial-null safe, auto-persist), 4 test (`622fa2d`). I-A-3 wire pinned UA+viewport: `scan_source` + `send_comment` signatures add `viewport=None`, `_run_scan_all_sources` + trending `POST /comment` router call `ensure_fingerprint(account.id)` sebelum invoke, `DEFAULT_USER_AGENT` upgrade Chrome 120→131, 2 test (source_collector forward kwargs + orchestrator pin per-account) (`d66f604`). Full suite **651 passed** (+13 since F7). Deploy: `alembic upgrade head` 004→005 clean, `sudo systemctl restart fb-bot-api fb-bot-worker fb-bot-beat` all active, smoke via SessionLocal: `ensure_fingerprint(1)` assigned UA Chrome 132 + viewport 1536x864, idempotent check (ua,w,h) tuple stable, DB row verified populated. Phase I-B (cookie rotation capture) + I-C (persistent profile) pending next — observe 24h per rollout plan §5. Roadmap: `.kiro/steering/phase-i-session-hardening.md`. HEAD `ac4f971`. |
| 2026-05-10 | phase F1+F2 template editor deploy live | F1 `TemplateService` single-active row invariant (upsert deactivates old, strip whitespace, reject empty) + `render_template` Formatter `_SafeDict` fallback missing-key → empty (None coerced ke ""). F1 router `GET /api/v1/template` (any auth role) + `PUT /api/v1/template` (admin-only, 400 empty, 422 missing field), 22 unit+router test. F2 UI `/template` admin-only: textarea 5000-char cap, placeholder help + preview card render `{author_name}`=Budi Santoso + `{text_snippet}`=jual laptop gaming…, dirty-state badge "Belum disimpan", Save+Reset button, last-updated label. TanStack Query `['template']` + invalidate on save. Nav item "Template" inserted between Sumber dan Accounts. Smoke: PUT create row id=1 id `halo {author_name}, gue tertarik sama {text_snippet}` → GET return persisted. Bundle 1740 modules 505KB/154.64KB gz. HEAD `443a86d`. |
| 2026-05-10 | phase F3 draft generator deploy live | F3 router endpoints `POST /api/v1/trending/{id}/draft` (admin, render active template via `render_template(template_text, author_name, text_snippet)`, flip status NEW/DRAFTED/SKIPPED → DRAFTED, 400 empty-template, 404 unknown, 409 already COMMENTED) + `POST /{id}/skip` (admin, → SKIPPED, 409 COMMENTED, 404 unknown). 11 new router test (total 25 at trending_router, all pass). Trending card UI: "Generate Draft" button aktif buat admin kalau status≠COMMENTED, show Loader2 spin saat mutation pending, show Re-draft label kalau DRAFTED, inline Textarea editor dengan tombol Tutup + Send (disabled placeholder Phase F5), tombol Skip sebelum Send, Skip hilang kalau status=SKIPPED. Toast sonner buat success/error. Admin-only `Sumber` filter dropdown (hide buat viewer). Smoke live: POST /trending/2/draft → `draft_text = "halo Jd Nayem, gue tertarik sama wow #foryou..."` + status=DRAFTED ✓. Rolled back DB ke NEW untuk next dev. Bundle 1741 modules 508KB/155.34KB gz. HEAD `dfba7ce`. |
| 2026-05-10 | phase F4a rate limit service deploy live | F4a `RateLimitService` 5 komen / 6 jam rolling-window: `check_allowed()` return `QuotaStatus(allowed, used, remaining, limit, window_hours, resets_at)` baca `comment_history` status SENT dalam window, `window_stats()` wrapper dict buat router, `record_send(...)` insert CommentHistory + preflight raise `RateLimitExceededError` kalau full (FAILED selalu lolos buat audit), SENT auto-flip `TrendingPost.status='COMMENTED'`. Constants `MAX_COMMENTS_PER_WINDOW=5`, `WINDOW_HOURS=6`. Router `GET /api/v1/rate-limit/status` any-auth, response `{quota: {allowed, used, remaining, limit, window_hours, resets_at}}`. 23 test total (16 service + 7 router). Smoke live: quota snapshot `{used:0, remaining:5, allowed:true}` ✓. Full suite **544 passed** (+56 dari phase F3). Split F4 plan jadi F4a (service) + F4b (Playwright sender, pending). HEAD `7d4f34b`. |
| 2026-05-10 | phase F4b playwright comment sender LIVE SMOKE VERIFIED | F4b `bot/modules/comment_sender.py` — `send_comment(post_url, comment_text, cookies, display_name, delay_range_ms=(50,150), headless=True)` → `SendResult(success, comment_text, post_url, fb_comment_id, error, checkpoint)`. Flow: validate inputs → launch headless Chromium → `create_session_context` cookies inject → goto `post_url` → detect login/checkpoint redirect (raise `CookieExpiredError`/`CheckpointRequiredError`) → `_find_composer` via `div[contenteditable="true"][role="textbox"][aria-label^="Comment as"]` (expand "Leave a comment" stub kalau ga ketemu) → `_type_humanlike` per-char via `page.keyboard.type(ch, delay=random(lo,hi))` → klik `div[role="button"][aria-label="Post comment"]` → `_wait_posted_comment` verify `div[role="article"][aria-label^="Comment by <display_name>"]` inner_text match 40-char prefix. `fb_comment_id=None` acceptable (FB composer ga expose stable id di DOM). Rate-limit guard OWNED by caller (F5 router), sender pure posting. Pre-probe via `scripts/probe_comment_dom.py` confirm selector stable di FB 2026. Mock test 11/11 pass (AsyncMock fake browser/context/page/textbox/post_btn). **LIVE SMOKE**: `scripts/smoke_comment_send.py 8` → post komen `"mantap bro"` ke `facebook.com/photo/?fbid=1331129762456586` pake akun Digi Markt → `success=True`, fb_comment_id=None, no error ✓. comment_history tetap 0 (expected, record_send tugas F5). Full suite **555 passed** (+11). HEAD `360e8d5`. |
| 2026-05-10 | phase F5 send endpoint + UI wiring deploy live | F5 router `POST /api/v1/trending/{id}/comment` admin-only, body `{comment_text}` (1-5000 char). Flow: 400 empty, 404 unknown, 409 COMMENTED, 400 missing post_url → `RateLimitService.check_allowed` preflight 429 kalau full → pick `FBAccount.status='ACTIVE'` + `cookies_encrypted IS NOT NULL` → 503 kalau kosong → `decrypt_cookies` → await `send_comment(...)` → sukses: `record_send(status='SENT', fb_comment_id=...)` auto-flip post COMMENTED, return `{result, post, quota}`. Error: `CookieExpiredError` → mark FBAccount EXPIRED + 503. `CheckpointRequiredError` → mark FBAccount CHECKPOINT + 503. Non-success `SendResult` → 502 + record FAILED (no status flip, no quota burn). Semua FAILED branch log ke CommentHistory buat audit. 11 new router test pake monkeypatch `server.routers.trending.send_comment` (happy, rate-limit-blocks-before-send, sender-failure-logs-failed, cookie-expired-marks-account, checkpoint-marks-account, 404/409/400/503 guards). UI Trending card: button Send enabled (admin + quota allowed + draftTrimmed≠empty), `api.sendComment(postId, text)` + `api.getRateLimitStatus()` → `useQuery ['rate-limit-status']` auto-refetch 30s. Quota banner Card baru di atas filter row: ikon Gauge + `used/limit`, window hours, relative reset time; `text-destructive` kalau `allowed=false`. onSuccess toast + invalidate `['trending']` + `['rate-limit-status']`. Send button disabled reasoning: `!isAdmin`, `!quota`, `!quota.allowed` (reset relative). Full suite **566 passed** (+11). Bundle 1740 modules, `index-DF24xtV9.js` 510.60 KB / 155.99 KB gz. Smoke: GET /rate-limit/status → `{used:0, remaining:5}` ✓. HEAD `45eda50`. |
| 2026-05-12 | post-F5 polish rollup deploy live | **F6** `GET /api/v1/history` list endpoint (status filter SENT/FAILED/PENDING, pagination `limit`/`offset`, post summary embed) + `/history` page w/ 25/page pagination (`d82ed4d`..`316cf17`). **Phase G hardening**: app-wide ErrorBoundary + global React Query error toast via queryCache/mutationCache.onError (`e653106`), rewrite README+add docs/ARCHITECTURE.md+docs/DEPLOY.md (`a3e8459`), route-level `lazy(() => import)` code-split + global QuotaWidget di header + dark-mode default, cleanup obsolete review-queue page + drafts/approvals routers (`9a7d808`..`f94333d`). **AI draft** `AIDraftService` OpenAI-compat sumopod.com (default MiniMax-M2.7-highspeed) + `POST /trending/{id}/ai-draft` admin-only dengan 15s per-user in-memory cooldown + UI sparkle wand button w/ dirty-check (`e33a1ac`..`d7e2ca7`). **Scanner audit** `ScannerRun` table + `GET /scanner/status` + `POST /scanner/run-now` + `ScannerIndicator` component (dot freshness) + admin Scan-now button + UTC-explicit ISO serialization fix (7-jam tz bug) (`ddb35d2`..`e2ba6c8`). **Cookie health** `bot/modules/fb_auth_probe.py` DOM login-wall probe, scanner + sender `CookieExpiredError` walaupun URL terlihat normal, global `AccountStatusBanner` surface EXPIRED/BLOCKED (`f2d69a9`..`2f31152`). **Photo viewer composer** scroll-to-bottom hydration + EN/ID multi-locale (`Berkomentar`/`Tulis komentar`) + text-based stub fallback (`ac488ea`..`c107c1a`). **Trending UX** reject stories/reel/watch URL 415 + shared `fb_url` util + badge "Belum komen" + Fresh badge via `localStorage.lastSeenAt` (`7bcaf6d`..`029144e`). HEAD `029144e`. |
| 2026-05-12 | phase F7 account recovery UX deploy live | F7-1 surface `CHECKPOINT` di `AccountStatusBanner` + `FBAccounts` card (status filter, amber warning block, `statusBadgeVariant`) (`ce0b17a`). F7-2 backend `POST /fb-accounts/{id}/re-validate` admin-only: decrypt stored cookie, call `validate_and_fetch_profile`, on success `mark_active_from_profile` (refresh fb_name/avatar, clear `cookies_expired_at`, failure_count=0), on `CookieValidationError` `mark_cookies_expired`; 400 manual, 404 unknown, 403 viewer. 5 router test (`0867b3a`..`f620b0f`). F7-3 backend `POST /fb-accounts/{id}/re-upload-cookie` admin-only: parse + validate new cookie, Fernet-encrypt, `replace_cookies` (keep label/notes/history), flip ACTIVE; 6 test cover happy / invalid-keeps-old / empty / manual-400 / unknown-404 / viewer-403 (`3e4bed4`). Test fixture fix: pre-import `server.main` supaya `Base.metadata` kenal `User` sebelum `create_all` (hilangin "no such table: users" saat test class dijalanin isolasi) (`e5d35e8`). F7-4..7 FE (`d976df3`): `api.reValidateFBAccount` + `api.reUploadFBCookie` helpers, Re-validate button on cookie-only card (`ShieldCheck`), Re-upload Cookie button conditional EXPIRED/CHECKPOINT, separate Re-upload Dialog reuse `CookieInstructions` + preview via `POST /preview-cookie` + confirm, `AccountStatusBanner` deep-link `?action=reupload` auto-open dialog on account load + strip param, EXPIRED card warning text update ("Klik Re-upload Cookie" instead of hapus+connect). F7-8 verify: full suite **638 passed** di rdpkhorur, dashboard build 2.76s bundle `FBAccounts-DJOLp5yx.js` 23.06 KB / 6.83 KB gz, `sudo systemctl restart fb-bot-api` active, smoke `GET /fb-accounts/current` 200 status=EXPIRED, `POST /1/re-validate` 200 `valid=false` (cookie live memang expired), unknown id → 404 ✓. HEAD `d976df3`. |

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
