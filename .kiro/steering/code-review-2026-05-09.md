# Code Review Findings — Fase 0 sampai Deploy+Monitoring

Tanggal: 2026-05-09
Scope: seluruh kode `D:\program\facebook-bot` (fase 0 → 5 + deploy + FB account refactor)
Reviewer: 4 independent subagent per domain (bot-core, bot-generation-data, server-backend, dashboard-deploy-migration)
Verdict: **FAIL di semua domain**. Tidak boleh push/deploy lagi sebelum blocker di bawah diberesin.

Total: 43 security concern, 50 logic error, 41 suggestion.

---

## Executive Summary (per domain)

| Domain                          | Pass | Security | Logic | Suggestions |
|---------------------------------|------|----------|-------|-------------|
| bot-core                        | ❌   | 0        | 9     | 8           |
| bot-generation-data             | ❌   | 3        | 16    | 11          |
| server-backend                  | ❌   | 15       | 9     | 10          |
| dashboard-deploy-migration      | ❌   | 25       | 16    | 12          |

---

## Blocker wajib fix sebelum deploy (prioritas P0)

### 1. Runtime crash — Celery meledak pas jalan
- `bot/tasks.py:54` — `Orchestrator()` dipanggil tanpa argumen, padahal `__init__` butuh SQLAlchemy `Session`. TypeError tiap run.
- `bot/tasks.py:103` & `:163` — panggil `orchestrator.process(post)` padahal method asli `process_collected_posts(raw_posts)`. AttributeError.
- `tests/test_celery_tasks.py:43` — tes lolos cuma karena `Orchestrator` di-`MagicMock` penuh → false-positive.
Dampak: tiap Celery beat tick = exception, nothing collected, nothing drafted.

### 2. RBAC bypass total (server/routers/auth.py)
- `POST /auth/register` tidak ada `Depends(require_role(Role.ADMIN))` setelah user pertama. Siapa pun di Internet bisa bikin akun `role=admin` (role di request diambil verbatim di line 116).
- `JWT_SECRET` default `"change-me"` → token bisa diforge trivially kalau env lupa di-set.
- `CREDENTIALS_KEY` fallback derive dari `JWT_SECRET_KEY` + string literal `"fallback-insecure-key"` → siapa pun yang pegang JWT secret bisa dekripsi kredensial FB di DB.

### 3. Dashboard build pasti gagal (dashboard/package.json)
- Versi halu: `typescript ~6.0.2`, `vite ^8.0.10`, `eslint ^10.2.1`, `@vitejs/plugin-react ^6.0.1`, `@types/node ^24.12.2`. Semua beyond versi yang exist di registry.
- Konsekuensi: `deploy.sh` step `[5/8] npm install` hard-fail → `dist/` kosong → nginx serve 403.

### 4. TLS belom ada (deploy/nginx/fb-bot.conf)
- `listen 80` doang. JWT + password login jalan plaintext.
- Tidak ada security header (HSTS, CSP, X-Content-Type-Options, dll).
- Tidak ada rate-limit di `/api/auth/login` — brute force unthrottled.

### 5. Migrasi 001 rusak untuk Postgres + gak ada FK/index
- `server_default=sa.text('1')` untuk boolean = SQLite-only (Postgres butuh `'true'`).
- `posts.target_id`, `drafts.post_id`, `approvals.draft_id/user_id`, `audit_logs.user_id` — semua **tanpa ForeignKey dan tanpa index**.
- `audit_logs.created_at` tanpa index → query audit langsung lemot.
- `created_at` columns nullable tanpa `server_default=func.now()`.

### 6. Scorer formula konflik sama config
- `bot/config/scoring_rules.json` deklarasi `weights.risk_penalty = -0.10` tapi `scorer.py:70` tambahin `+ risk` mentah-mentah (tanpa kali weight). Tiga risk tag = -0.9 alih-alih -0.09. Weight key dead. Config dan code beda maksud.

### 7. Parser K/M locale bug (bot/modules/parser.py:207)
- Collector force locale `id-ID` (thousands=`.`, decimal=`,`), tapi parser strip koma dan treat `.` sebagai decimal. `"1,2K"` render jadi `int(float("12")*1000) = 12000` padahal semestinya 1200.

### 8. Race condition di fb_account & rate_guard
- `server/services/fb_account_service.py:121` — `get_next_available` flip COOLDOWN→ACTIVE tanpa `SELECT ... FOR UPDATE` / row lock. Dua worker bisa pick akun sama bareng.
- `bot/modules/rate_guard.py:18` — `check_and_reserve` check-then-act tanpa lock. Global & per-target limit bisa bablas kalau concurrent.
- `rate_guard` memory leak: `_target_requests[target_id]` append selamanya, nggak di-prune.

### 9. Token storage + refresh race (dashboard)
- `authStore.ts` + `api.ts` — JWT access+refresh di `localStorage`, role juga persisted di situ dan dipake sebagai trust input (`ReviewQueue.tsx:95`). XSS = game over.
- `api.ts:17` — refresh race: N request 401 bareng → N `tryRefresh()` independen → refresh token pertama invalidate sisanya → spurious logout.
- `api.ts:49` — `tryRefresh()` tulis ke localStorage doang, Zustand store gak ke-update.
- `api.ts:25` — `localStorage.clear()` nyapu seluruh origin state, bukan cuma auth.

### 10. systemd gak aman
- `deploy/systemd/fb-bot-api.service:12` — `uvicorn --host 0.0.0.0:8100` → exposed di semua interface VPS. Harus `127.0.0.1` karena ada nginx di depan.
- Ketiga service: `Restart=always` tanpa `StartLimitBurst`/`StartLimitIntervalSec` → crash loop hammer forever.
- Tidak ada sandboxing: `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes` semua hilang.
- `fb-bot-beat.service` tanpa single-instance guard → dua beat = double schedule.

---

## Blocker penting tapi tier di bawah (P1)

### Bot layer
- `bot/modules/draft_engine.py:91` — async bridge spawn `ThreadPoolExecutor` dengan timeout=60, tapi future gak di-cancel → thread keep running, pool.__exit__ block.
- `bot/modules/draft_engine.py:199` — `_draft_fingerprints` set mutated dari sync + worker thread, gak thread-safe, plus side-effect di `_validate_draft` yang dipanggil speculatively.
- `bot/modules/ai_generator.py:148` — fallback chain OpenAI↔Ollama hilang; kalau provider utama down, gak ada cross-fallback.
- `bot/modules/ai_generator.py:176` — Bearer token gak cek `api_key` non-empty. Kalau `OPENAI_API_KEY` kosong, kirim `"Bearer "` ke base_url apa pun → bisa exfil prompt.
- `bot/modules/ai_generator.py:164` — generic `Exception` branch bikin non-retryable error (JSONDecodeError) retry max_retries+1 kali.
- `bot/modules/collector.py:242` — Graph API `access_token` di URL query string → bocor ke access log/proxy log.
- `bot/modules/collector.py:232` — API-first silently fallback ke scrape kalau token kosong, bukan surface config error.
- `bot/modules/collector.py:251` — Graph API token expiry (code 190) di-treat `BlockDetectedError` → circuit breaker trip per-target padahal itu global credential issue.
- `bot/modules/collector.py:135` — Playwright cleanup gap: page/context gak explicit close, partial failure bisa leave dangling context.
- `bot/modules/notifier.py:1` — **zero rate limit + zero dedup**, padahal requirement eksplisit. Block-detected alert bakal spam tiap Celery cycle.
- `bot/modules/notifier.py:121` — `parse_mode='Markdown'` + user-provided content → Telegram reject seluruh message kalau ada `_`/`*` yang break markup → alert silently dropped.
- `bot/celery_app.py:38` — beat schedule cuma `collect-all-targets`. Daily/weekly report + health check task ada di notifier tapi gak pernah dijadwal. Dead code.
- `bot/modules/orchestrator.py:37` — `get_existing_ids()` no filter → load seluruh post_id yang pernah ada ke memory tiap run. Unbounded growth.
- `bot/services/post_service.py:24` — `save_post` baca `post_data['text']` tapi parser output pake `'text_snippet'`. Text field di DB bakal kosong.
- `bot/modules/detector.py:21` — dua sumber blacklist (`keywords.json.blacklist` vs `blacklist.json`); salah satu bakal drift.
- `bot/modules/detector.py:33` — `_seen_ids` in-memory only. Celery multi-worker → tiap worker punya set sendiri → duplicate detection bocor antar worker.
- `bot/modules/scheduler.py:34` — `rate_guard` diterima di `__init__` tapi gak pernah dipanggil di `get_runnable_targets`. Either wire up atau buang.
- `bot/modules/circuit_breaker.py:68` — setelah cooldown expire, `get_status()` return DEGRADED tapi `_status` masih SUSPENDED. Stale state.
- `bot/modules/recovery.py:27` — `save_snapshot` pake `write_text` langsung, non-atomic. Crash mid-write → corrupt snapshot. Harus tmp + `os.replace()`.
- `bot/modules/logger.py` — `FileHandler` tanpa rotation → unbounded log growth di VPS.

### Server layer
- `server/routers/auth.py:47` — login rawan timing attack (short-circuit sebelum bcrypt).
- `server/routers/auth.py:66` — refresh token gak ada rotation/revocation. Token curian valid 7 hari, gak bisa di-invalidate.
- `server/routers/auth.py:77` — `int(payload['sub'])` tanpa guard → ValueError 500 bukan 401 kalau `sub` non-numeric.
- `server/main.py:36` — CORS `allow_credentials=True` + `allow_methods=['*']` + `allow_headers=['*']` + default origin hardcoded `http://localhost:5173`. Kalau env lupa → wildcard credentialed.
- `server/main.py:63` — SPA fallback serve `DASHBOARD_DIR / path.lstrip('/')` tanpa `resolve()` + check dalam DASHBOARD_DIR → potential path traversal.
- `server/websocket.py:39` — JWT di query string `?token=...` → bocor ke log/history/Referer. Pindahin ke `Sec-WebSocket-Protocol` atau `Authorization`.
- `server/websocket.py:43` — `decode_token` raise `HTTPException(401)` di WS handler → propagate 500.
- `server/websocket.py:27` — broadcast swallow semua exception, disconnected client tetep di active_connections forever.
- `server/routers/fb_accounts.py:34` — `require_role('admin')` (plain string). `auth.require_role` iter dan akses `r.value` → string gak punya `.value` → AttributeError runtime. Saat ini router di-disable di main, tapi kalau re-enable langsung 500 semua endpoint.
- `server/routers/settings.py:20` — `PUT /settings` terima `dict` tanpa validasi + tanpa audit log.
- `server/services/fb_account_service.py:133` — timezone handling half-patched. `cooldown_until` column `DateTime` (bukan `DateTime(timezone=True)`) → tzinfo dropped di sqlite tapi dipertahankan di Postgres. Standardize.
- `server/routers/drafts.py:13` + `server/routers/posts.py:13` — cuma `get_current_user`, gak ada tenant/owner scoping. Semua user authenticated liat semua draft/post.
- `server/routers/approvals.py:40` — `'edit'` action di docstring tapi gak ada di if/elif chain.
- `server/routers/approvals.py:71` — commit tanpa try/except + `db.rollback()`.
- `server/database.py:8` — `DATABASE_URL` default `sqlite:///bot/data/app.db` silent di prod.
- `server/models.py:58` — `post_timestamp: Mapped[datetime]` tapi `nullable=True`. Harus `Mapped[datetime | None]`.

### Deploy layer
- `deploy/deploy.sh:16` — `git pull origin main` tanpa backup/snapshot + tanpa rollback path.
- `deploy/deploy.sh:29` — `alembic upgrade head` tanpa backup DB.
- `deploy/deploy.sh:25` — `playwright install chromium 2>/dev/null || true` silence semua error.
- `deploy/deploy.sh:35` — `npm install` bukan `npm ci` → non-reproducible.
- `deploy/deploy.sh:44` — `sudo cp` overwrite `/etc/systemd/system/*` tanpa backup.
- `deploy/nginx/fb-bot.conf:8` — `proxy_set_header Host $host` trust client Host. Pin ke `$server_name`.
- `alembic/env.py:39` — `context.configure` gak ada `render_as_batch=True` → SQLite ALTER bakal fail ke depan.
- `alembic/env.py:9` — FBAccount model gak di-import (kalaupun ditambah di models, autogenerate blind).

### Frontend
- `App.tsx:12` — `ProtectedRoute` cuma cek presence, gak cek expiry.
- `App.tsx:20` — gak ada catch-all route (`path="*"`).
- `App.tsx:22` — `/login` gak guarded; user logged-in liat login form.
- `FBAccounts.tsx` + `api.ts:77-97` — endpoint + page ada, route commented out, **dan tabel `fb_accounts` gak ada di migrasi**. Kalau re-enable, crash langsung.

---

## Non-blocking suggestions (pilih sesuai waktu)

### Tests coverage bolong
- `tests/test_scheduler.py` — no test cb-cooldown-expired re-include, no priority tie.
- `tests/test_pipeline.py` — no borderline below-threshold case.
- `tests/test_detector.py` — no unsupported-language path test.
- `tests/test_parser.py` — no locale-flipped K/M case (butuh setelah parser fixed).
- `tests/test_collector.py` — no Playwright lifecycle test.
- `tests/test_notifier.py` — no dedup/rate-limit test (butuh setelah feature ada).
- `tests/test_auth.py` — no `/register` bypass test, no JWT forgery, no expired token, no refresh reuse. Juga expect 403 padahal semantically 401.
- `tests/test_fb_account_service.py` — encrypt/decrypt semua di-mock, zero real round-trip.

### Code quality
- MD5 fingerprint → ganti blake2b/sha256.
- `parser._detect_language` naïve bag-of-words → fasttext-lid/langdetect.
- AI generator hardcode `max_tokens=300`/`temperature=0.7` → pindahin ke config.
- Tidak ada prompt injection sanitization on `post['text_snippet']`.
- Collector User-Agent statis → rotate pool + viewport/timezone randomize.
- Notifier: no 429/5xx retry → tenacity/backoff.
- `draft_service.approve/reject` no `db.rollback()` on failure.
- `celery_app.task_acks_late=True` tanpa `task_reject_on_worker_lost=True` → task drop on crash.
- `models.py` — tambah index di `Post.status`, `Draft.status`, `FBAccount.status`, `Post.collected_at`.
- `user_service.py` — no change-password/reset-password flow.
- Router responses hand-rolled dict → pakai Pydantic `response_model`.
- Frontend `catch (err: any)` → `unknown` + narrow.
- `confirm()`/`alert()` → real modal + toast (react-hot-toast).
- No `<ErrorBoundary>`.
- A11y: no landmarks/skip-link, no `aria-current`, no `aria-live` error.
- Vite `manualChunks` belum di-set → initial load bloated.
- PII columns (`targets.url`, `posts.author_id`, `posts.text_snippet`, `drafts.text`, `approvals.edited_text`) plaintext → EncryptedType.

---

## Rekomendasi urutan fix

1. **Stop deploy.** Activity log `deploy+monitoring` claim "204 tests passed" padahal tasks.py secara factual broken — itu false green.
2. **Fix tasks.py** dulu (pass `db`, pake `process_collected_posts`), tambahin integration test yang **gak** di-MagicMock penuh.
3. **Fix RBAC + secret management** di server (/register guard, fail-fast secret, rotate refresh token, pindahin WS token).
4. **Fix dashboard package.json** (versi beneran), build locally, confirm dist generated.
5. **TLS + nginx hardening** (certbot, HSTS, limit_req, Host pinning, security headers).
6. **Fix migration 001** (`server_default='true'`, tambah FK+index, tambah `fb_accounts` table atau hapus dead code).
7. **Fix scorer formula** + **parser K/M locale**.
8. **Fix race** di `fb_account_service` + `rate_guard` (row lock / atomic dict op).
9. **systemd hardening** (bind 127.0.0.1, StartLimitBurst, sandboxing flags).
10. **Notifier dedup + rate limit** + **Celery beat add daily/weekly/health** schedule.
11. **Frontend**: singleton refresh, httpOnly cookie migration, expiry check, catch-all route, FBAccounts dead-code cleanup.
12. **Ulangi full review** setelah P0+P1 kelar.

---

## Catatan proses

Review ini dijalankan 4 subagent paralel (bot-core, bot-generation-data, server-backend, dashboard-deploy-migration). Tiap agent baca file dari nol tanpa konteks implementer. Tidak ada reviewer yang verify karyanya sendiri — fail-closed.

Full raw JSON findings per domain: lihat chat log sesi ini (timestamp 2026-05-09 ~16:15).
