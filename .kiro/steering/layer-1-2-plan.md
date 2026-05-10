# Layer 1 + Layer 2 Implementation Plan — Trending Scanner & Comment Draft Assistant

> **For Apis (AI assistant):** Pakai TDD per task. RED test dulu, baru GREEN. Commit per task selesai. Living plan — update status setelah tiap task beres.

**Goal:** Dashboard jadi tools buat satu user Facebook yang udah connect cookie session-nya, otomatis scan home feed + grup + page, deteksi post yang lagi rame, generate draft komen promosi dari template yang di-setup user. User klik Send per komen — **gak ada auto-fire ever**.

**Architecture:**
- Layer 1 (read-only): Playwright + cookie session → scrape feed/grup/page → parser → scorer → DB `trending_posts` table. Periodic via Celery beat tiap 15 menit.
- Layer 2 (human-approve): Dashboard list post trending → tombol "Generate Draft" (pake template user) → user edit → klik "Send" → Playwright post komen via cookie session yang sama → catat history.
- Layer 3 (Meta Graph API Pages) — **deferred**, bukan scope MVP.

**Tech Stack:** Playwright (chromium), httpx, Celery + Redis, SQLAlchemy (SQLite), Alembic, Fernet crypto, FastAPI, React + shadcn/ui, TanStack Query, Zustand.

**Acceptance Criteria (MVP Done When):**
1. User bisa paste raw cookie string Facebook di dashboard, dashboard validate dengan hit `m.facebook.com/me` → tampilin preview `{name, fb_user_id, profile_pic}` → user confirm save (Fernet encrypt, masuk ke `fb_accounts.cookies_encrypted`).
2. Dashboard page `/sources` bisa add/edit/delete sumber scan (type: `home_feed`, `group`, `page`), plus set filter keyword (include/exclude) per sumber.
3. Celery beat jalan tiap 15 menit, loop semua sumber enabled, scrape via Playwright headless pake cookie user, parse post, score by engagement velocity + absolute, simpan ke `trending_posts` kalau lulus threshold.
4. Dashboard page `/trending` tampilin list post trending (sort by score desc), card per post: foto+author, excerpt teks, metric engagement, timestamp, source, link ke FB.
5. Dashboard page `/templates` (bagian dari settings): 1 textarea buat template promosi, save, load.
6. Card post trending punya tombol "Generate Draft" → draft komen muncul di textarea editable (pre-filled dari template), user edit bebas, klik "Send". Kalau Send, backend Playwright post komen, log ke `comment_history`. Kalau Skip, post ditandai `skipped`.
7. Dashboard page `/history` tampilin komen yang udah di-send (timestamp, post link, komen text, status success/failed).
8. Session cookies expired handling: kalau scrape gagal karena redirect ke login page / cookie invalid → mark `fb_accounts.status=EXPIRED`, notif di dashboard ("Cookie lo expired, re-connect dulu"), bot pause sampai user re-paste.
9. Rate limit soft safeguard: max 5 send komen per 6 jam (hardcoded MVP, tune later), dashboard tampilin remaining quota.
10. Full test suite >= 320 pass (current baseline 294 + fitur baru).

---

## Task Status Legend
- `[ ]` Pending
- `[~]` In progress
- `[x]` Completed
- `[!]` Blocked

---

## Phase A — Data Model & Cookie Session (Backend)

### Task A1: Migration `003_cookie_session_and_sources.py` `[x]`

**Objective:** Tambah kolom cookies ke `fb_accounts`, tabel baru `sources`, `trending_posts`, `comment_history`, `comment_templates`.

**Files:**
- Create: `alembic/versions/003_cookie_session_and_sources.py`

**Schema changes:**
```python
# fb_accounts — ADD columns
cookies_encrypted: Text, nullable=True
fb_user_id: String(100), nullable=True, index=True
fb_name: String(200), nullable=True
fb_profile_pic_url: String(500), nullable=True
cookies_expired_at: DateTime(timezone=True), nullable=True
# (email_encrypted + password_encrypted jadi nullable=True — dipertahankan backward compat tapi gak wajib)

# NEW: sources
CREATE TABLE sources (
  id INTEGER PRIMARY KEY,
  type VARCHAR(20) NOT NULL,  -- 'home_feed' | 'group' | 'page'
  label VARCHAR(200) NOT NULL,
  url VARCHAR(500),            -- null untuk home_feed
  fb_entity_id VARCHAR(100),   -- group_id / page_id
  keywords_include TEXT,       -- JSON array
  keywords_exclude TEXT,       -- JSON array
  enabled BOOLEAN DEFAULT 1,
  last_scanned_at DATETIME(tz),
  created_at DATETIME(tz) DEFAULT now
)
CREATE INDEX idx_sources_enabled ON sources(enabled);

# NEW: trending_posts
CREATE TABLE trending_posts (
  id INTEGER PRIMARY KEY,
  fb_post_id VARCHAR(100) UNIQUE NOT NULL,
  source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  author_name VARCHAR(200),
  author_fb_id VARCHAR(100),
  text_snippet TEXT,
  post_url VARCHAR(500),
  likes INTEGER DEFAULT 0,
  comments INTEGER DEFAULT 0,
  shares INTEGER DEFAULT 0,
  reactions_total INTEGER DEFAULT 0,
  score REAL DEFAULT 0.0,
  velocity REAL DEFAULT 0.0,     -- reactions/hour
  post_timestamp DATETIME(tz),
  collected_at DATETIME(tz) DEFAULT now,
  status VARCHAR(20) DEFAULT 'NEW',  -- 'NEW' | 'DRAFTED' | 'COMMENTED' | 'SKIPPED'
  thumbnail_url VARCHAR(500)
)
CREATE INDEX idx_trending_score ON trending_posts(score DESC);
CREATE INDEX idx_trending_status ON trending_posts(status);
CREATE INDEX idx_trending_source ON trending_posts(source_id);

# NEW: comment_templates (1 row buat MVP, tapi siapin schema multi-template buat future)
CREATE TABLE comment_templates (
  id INTEGER PRIMARY KEY,
  name VARCHAR(100) NOT NULL DEFAULT 'default',
  template_text TEXT NOT NULL,
  is_active BOOLEAN DEFAULT 1,
  created_at DATETIME(tz) DEFAULT now,
  updated_at DATETIME(tz) DEFAULT now
)

# NEW: comment_history
CREATE TABLE comment_history (
  id INTEGER PRIMARY KEY,
  trending_post_id INTEGER NOT NULL REFERENCES trending_posts(id) ON DELETE CASCADE,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  comment_text TEXT NOT NULL,
  fb_comment_id VARCHAR(100),   -- balikan dari FB setelah post
  status VARCHAR(20) NOT NULL,  -- 'SENT' | 'FAILED' | 'PENDING'
  error_message TEXT,
  sent_at DATETIME(tz) DEFAULT now
)
CREATE INDEX idx_comment_history_sent ON comment_history(sent_at DESC);
CREATE INDEX idx_comment_history_post ON comment_history(trending_post_id);
```

**Verification:** `alembic upgrade head` on server, `\d+ sources` (atau `.schema sources` di sqlite), check semua index ada.

**Commit:** `feat: migration for cookie session, sources, trending posts, templates, history`

---

### Task A2: SQLAlchemy Models Update `[x]`

**Objective:** Update `server/models.py` — extend `FBAccount`, add `Source`, `TrendingPost`, `CommentTemplate`, `CommentHistory`.

**Files:**
- Modify: `server/models.py`

**Verification (RED):** `pytest tests/test_models.py` — nambah test file:
```python
def test_fb_account_has_cookie_columns():
    assert hasattr(FBAccount, "cookies_encrypted")
    assert hasattr(FBAccount, "fb_user_id")

def test_source_model_exists():
    assert Source.__tablename__ == "sources"

def test_trending_post_model_has_score():
    assert hasattr(TrendingPost, "score")

# dst untuk CommentTemplate, CommentHistory
```

**Commit:** `feat: add cookie/source/trending_post/template/history models`

---

### Task A3: Cookie Parser & Validator Service `[x]`

**Objective:** Service yang terima raw cookie string (format `name=value; name=value; ...`), parse ke dict, extract cookie penting (`c_user`, `xs`, `datr`, `fr`, `sb`, `wd`, `presence`), validate dengan GET `https://m.facebook.com/me` → parse response buat nama + fb_user_id + profile pic URL.

**Files:**
- Create: `server/services/cookie_session_service.py`
- Create: `tests/test_cookie_session_service.py`

**Key functions:**
```python
def parse_cookie_string(raw: str) -> dict[str, str]
def validate_and_fetch_profile(cookies: dict) -> ProfileInfo | None
def serialize_cookies(cookies: dict) -> str  # buat disimpan ke DB
```

**Verification:** unit tests dengan mock httpx client, cover:
- Parse happy path
- Parse with duplicate keys (pick last)
- Parse malformed (missing `=`, kosong)
- Validator happy: mock response HTML yang ada `"USER_ID":"123456"` pattern
- Validator invalid cookie: mock redirect ke login page → return None
- Validator network error → raise exception

**Commit:** `feat: cookie parser + profile fetcher (m.facebook.com)`

---

### Task A4: Encrypt/Decrypt Cookies via Existing Fernet `[x]`

**Objective:** Pake `server/crypto.py` yang udah ada buat encrypt serialized cookie string. Tambah helper `encrypt_cookies(raw: str) -> str` + `decrypt_cookies(enc: str) -> str`.

**Files:**
- Modify: `server/crypto.py` (tambah 2 helper, reuse existing Fernet)
- Modify: `tests/test_crypto.py` (tambah roundtrip test)

**Commit:** `feat: extend crypto module with cookie helpers`

---

### Task A5: Router `/api/v1/fb-accounts/connect-cookie` `[x]`

**Objective:** Endpoint POST body `{raw_cookies: str}` → parse + validate + preview → return `{ok: true, preview: {name, fb_user_id, profile_pic_url}}` tanpa save. Endpoint POST `/fb-accounts/confirm-cookie` body `{label: str}` → save (pake session cookies terakhir yang tersimpan di cache 5 menit, atau re-validate lagi).

Desain sederhana: 1 endpoint `POST /fb-accounts/connect` body `{label, raw_cookies}` → validate → encrypt → save langsung (atomic). Preview di-tunjukin via frontend sebelum submit form lewat endpoint terpisah `POST /fb-accounts/preview-cookie` body `{raw_cookies}` → return preview tanpa save.

**Files:**
- Modify: `server/routers/fb_accounts.py` — tambah 2 endpoint
- Modify: `dashboard/src/services/api.ts` — tambah method `previewCookie(raw)` + `connectCookie({label, raw})`
- Modify: `tests/test_fb_accounts_router.py` — tambah `TestCookieConnect`

**Acceptance:**
- Preview endpoint gak save, cuma return profile info
- Connect endpoint enforce single-account (kalo udah ada, return 409)
- Connect encrypt cookies pake Fernet
- Invalid cookie → 400 "Cookie tidak valid atau sudah expired"

**Commit:** `feat: cookie-based fb account connect flow`

---

## Phase B — Frontend: Cookie Connect UX

### Task B1: Update `FBAccounts.tsx` — Tab Cookie vs Manual `[x]`

**Objective:** Setup page sekarang punya 2 tab: "Cookie (Direkomendasikan)" dan "Manual". Cookie tab ada textarea gede buat paste cookie string + tombol "Preview Akun". Setelah preview sukses, tampil card preview (foto, nama, fb_user_id) + field label + tombol "Simpan". Manual tab tetep kayak existing.

**Files:**
- Modify: `dashboard/src/pages/FBAccounts.tsx`
- Create: `dashboard/src/components/CookieInstructions.tsx` — collapsible "Gimana cara dapet cookie?" dengan step-by-step pake extension Cookie-Editor.

**Instructions content (ID casual):**
```
1. Install extension "Cookie-Editor" di Chrome/Firefox/Edge
2. Buka facebook.com, login kayak biasa
3. Klik icon extension → tab "Export" → pilih "Header String"
4. Copy hasilnya, paste di sini
5. Klik "Preview Akun" buat verify
```

**Commit:** `feat: cookie connect UI with preview card`

---

## Phase C — Sources Management (CRUD Backend + UI)

### Task C1: Sources Service `[x]`

**Objective:** CRUD service untuk sources.

**Files:**
- Create: `server/services/source_service.py`
- Create: `tests/test_source_service.py`

**Methods:** `create_source`, `list_sources`, `get_source`, `update_source`, `delete_source`, `toggle_enabled`.

**Commit:** `feat: source service with CRUD`

---

### Task C2: Sources Router `/api/v1/sources` `[x]`

**Objective:** REST endpoints. Admin-only (pake existing `require_admin`).

**Files:**
- Create: `server/routers/sources.py`
- Modify: `server/main.py` — include router
- Create: `tests/test_sources_router.py`

**Endpoints:**
- `GET /sources` — list
- `POST /sources` — create `{type, label, url?, keywords_include, keywords_exclude, enabled}`
- `PATCH /sources/{id}` — partial update
- `DELETE /sources/{id}`
- `POST /sources/{id}/toggle` — enable/disable toggle

**Commit:** `feat: sources CRUD router`

---

### Task C3: Sources Page `/sources` Frontend `[x]`

**Objective:** Dashboard page dengan list sources + modal create/edit. Type picker (home_feed / group / page), URL input (disabled untuk home_feed), keyword include/exclude sebagai chip input.

**Files:**
- Create: `dashboard/src/pages/Sources.tsx`
- Modify: `dashboard/src/App.tsx` — route `/sources` AdminRoute
- Modify: `dashboard/src/components/app-header.tsx` — nav item "Sumber" untuk admin
- Modify: `dashboard/src/services/api.ts` — sources methods

**Acceptance:** bisa add home_feed sekali (enforce max 1 home_feed), unlimited group/page, toggle enable, delete with confirm.

**Commit:** `refactor: sources page UI with chip keyword input`

---

## Phase D — Scanner & Scorer

### Task D1: Playwright Cookie Injection Helper `[ ]`

**Objective:** Helper yang load decrypted cookies ke Playwright context sebelum scrape.

**Files:**
- Create: `bot/modules/fb_session.py`
- Create: `tests/test_fb_session.py` (mock Playwright)

**Key function:**
```python
async def create_session_context(cookies_dict: dict, user_agent: str) -> BrowserContext
```

**Commit:** `feat: playwright cookie session helper`

---

### Task D2: Scanner Refactor — Source-Based `[ ]`

**Objective:** Refactor `bot/modules/collector.py` supaya accept `Source` model sebagai input (bukan targets.json). Support 3 types:
- `home_feed` → `https://www.facebook.com/?sk=h_chr` (chronological) scroll 3x, extract post
- `group` → `https://www.facebook.com/groups/{fb_entity_id}` scroll 3x
- `page` → `https://www.facebook.com/{fb_entity_id}/posts` scroll 2x

**Files:**
- Modify: `bot/modules/collector.py`
- Modify: `tests/test_collector.py`

**Output:** list `RawPost` dicts dengan `fb_post_id, author_name, author_fb_id, text, likes, comments, shares, reactions_total, post_timestamp, post_url, thumbnail_url`.

**Anti-detection hygiene (baseline wajib):**
- Random delay 3-8 detik antar scroll (gaussian)
- Random viewport size dari preset realistic
- User-agent konsisten per session (simpan di fb_accounts atau env)
- Respect `last_scanned_at` — skip kalau belum 15 menit

**Commit:** `refactor: collector source-based with 3 scrape modes`

---

### Task D3: Scorer — Trending Heuristic `[ ]`

**Objective:** Tambah `score_trending(post: RawPost) -> tuple[score, velocity]` function. Default formula MVP:

```
age_hours = (now - post_timestamp) / 3600
velocity = reactions_total / max(age_hours, 0.5)
score = velocity * 0.7 + reactions_total * 0.3

# Threshold filter
is_trending = (
    age_hours < 24 and (velocity >= 50 or reactions_total >= 100)
)
```

**Files:**
- Modify: `bot/modules/scorer.py` — tambah function (jangan break existing `score_post`)
- Modify: `tests/test_scorer.py` — tambah `TestTrendingScorer`

**Edge cases:**
- `post_timestamp` null → `age_hours = 1.0` fallback
- Reactions 0 → velocity 0, gak trending
- Post umur < 30 menit → cap `age_hours = 0.5` biar velocity gak infinity

**Commit:** `feat: trending scorer with velocity + absolute heuristic`

---

### Task D4: Keyword Filter `[ ]`

**Objective:** Filter post berdasarkan `source.keywords_include` dan `keywords_exclude`. Case-insensitive, word boundary match.

**Files:**
- Create: `bot/modules/keyword_filter.py`
- Create: `tests/test_keyword_filter.py`

**Behavior:**
- `include` kosong → pass semua
- `include` isi → post.text harus match minimal 1 keyword
- `exclude` selalu di-apply — match = skip

**Commit:** `feat: keyword include/exclude filter`

---

### Task D5: Scanner Celery Task `[ ]`

**Objective:** Task `scan_all_sources` yang jalan tiap 15 menit. Flow: load sources enabled → loop → collector.scrape(source) → keyword_filter → scorer → upsert ke `trending_posts`.

**Files:**
- Modify: `bot/tasks.py` — replace/rename `collect_all_targets` dengan `scan_all_sources`
- Modify: `bot/celery_app.py` — beat schedule `scan_all_sources` every 15m
- Modify: `tests/test_celery_tasks.py`

**Upsert logic:** ON CONFLICT `fb_post_id` DO UPDATE (refresh metric + score kalau post udah ada). Jangan update `status` kalau udah `DRAFTED/COMMENTED/SKIPPED` (preserve user state).

**Commit:** `feat: scan_all_sources celery task with 15m beat`

---

### Task D6: Cookie Expiry Detection `[ ]`

**Objective:** Kalau collector detect redirect ke login / response kosong karena unauth, mark `FBAccount.status = EXPIRED` + set `cookies_expired_at = now`. Task berhenti bekerja sampai user re-connect.

**Files:**
- Modify: `bot/modules/collector.py` — raise `CookieExpiredError`
- Modify: `bot/tasks.py` — catch error, update status, log
- Modify: `server/routers/fb_accounts.py` — expose `status` di `/current` response supaya UI bisa tampilin warning banner

**Commit:** `feat: graceful cookie expiry handling`

---

## Phase E — Trending Page Frontend

### Task E1: API `/api/v1/trending` `[ ]`

**Objective:** `GET /trending?limit=50&status=NEW` → list trending posts sort by score desc.

**Files:**
- Create: `server/routers/trending.py`
- Modify: `server/main.py`
- Create: `tests/test_trending_router.py`

**Response schema:**
```json
{
  "posts": [{
    "id": 1, "fb_post_id": "...", "author_name": "...",
    "text_snippet": "...", "post_url": "...", "thumbnail_url": "...",
    "likes": 120, "comments": 30, "shares": 5, "reactions_total": 155,
    "score": 88.5, "velocity": 72.3,
    "post_timestamp": "...", "status": "NEW",
    "source": {"id": 1, "type": "group", "label": "..."}
  }]
}
```

**Commit:** `feat: trending posts router`

---

### Task E2: Trending Page `/trending` `[ ]`

**Objective:** Dashboard page pake TanStack Query, auto-refetch tiap 30 detik. Card per post (responsive grid). Sort dropdown (score/velocity/recent). Filter by source dropdown. Filter by status (NEW/DRAFTED/SKIPPED/COMMENTED).

**Files:**
- Create: `dashboard/src/pages/Trending.tsx`
- Modify: `dashboard/src/App.tsx` — route `/trending` (default home, replace ReviewQueue as landing)
- Modify: `dashboard/src/components/app-header.tsx` — nav "Trending" primary

**Card layout:**
```
[thumb] Author Name • Source Label • 2 jam lalu
        Text excerpt (3 lines max, clamp)
        ❤️ 120  💬 30  🔁 5  •  Score 88.5
        [Generate Draft] [Skip] [Lihat di FB ↗]
```

**Commit:** `feat: trending page with source/status filter`

---

## Phase F — Template + Draft + Send Flow

### Task F1: Template Service + Router `[ ]`

**Objective:** CRUD template (MVP single default). Endpoint `GET /template` return active, `PUT /template` body `{template_text}` update.

**Files:**
- Create: `server/services/template_service.py`
- Create: `server/routers/templates.py`
- Modify: `server/main.py`
- Create: `tests/test_template_service.py`, `tests/test_templates_router.py`

**Commit:** `feat: comment template service + router`

---

### Task F2: Template Page / Settings Tab `[ ]`

**Objective:** Dashboard page `/settings` (atau section di existing settings), textarea gede, save button, counter karakter, preview bagaimana kelihatan.

**Files:**
- Create: `dashboard/src/pages/Settings.tsx` (atau extend existing kalau ada)
- Modify: `dashboard/src/App.tsx`
- Modify: `dashboard/src/components/app-header.tsx` — nav "Settings"

**Commit:** `feat: template editor page`

---

### Task F3: Draft Generator — Source from Template `[ ]`

**Objective:** Endpoint `POST /api/v1/trending/{post_id}/draft` → generate draft dari template aktif → return `{draft_text}` + update `trending_posts.status = 'DRAFTED'`.

MVP: template cuma string literal, return as-is. Nanti bisa support placeholder `{author_name}` dkk.

**Files:**
- Modify: `server/routers/trending.py`
- Modify: `tests/test_trending_router.py`

**Commit:** `feat: draft generator endpoint`

---

### Task F4: Send Comment via Playwright `[ ]`

**Objective:** Backend module yang pake Playwright pake session cookies → buka post URL → tunggu comment input muncul → type text (with per-char delay) → click submit → verify komen muncul di list → extract `fb_comment_id` kalau bisa.

**Files:**
- Create: `bot/modules/comment_sender.py`
- Create: `tests/test_comment_sender.py` (mock Playwright)

**Rate limit guard:**
- Cek `comment_history` 6 jam terakhir
- Kalau >= 5 → raise `RateLimitedError`
- Random delay 30-120 detik antar send (kalau user klik rapid)

**Commit:** `feat: comment sender module with rate limit guard`

---

### Task F5: Send Endpoint + UI Wiring `[ ]`

**Objective:** `POST /api/v1/trending/{post_id}/comment` body `{comment_text}` → panggil `comment_sender.send()` → save ke `comment_history` → update `trending_posts.status = 'COMMENTED'`. Return `{ok, fb_comment_id?, error?}`.

Dashboard card: tombol "Generate Draft" expand inline ke textarea editable + tombol "Send" / "Cancel". Skip tombol set status SKIPPED langsung tanpa mutate FB.

**Files:**
- Modify: `server/routers/trending.py`
- Modify: `dashboard/src/pages/Trending.tsx`
- Modify: `dashboard/src/services/api.ts`

**Commit:** `feat: send comment endpoint + card inline draft UI`

---

### Task F6: History Page `/history` `[ ]`

**Objective:** List komen yang udah di-send, sort by sent_at desc. Card/table dengan: timestamp, post (clickable to FB), comment text, status badge (SENT/FAILED), error message kalau ada.

**Files:**
- Create: `server/routers/history.py`
- Create: `dashboard/src/pages/History.tsx`
- Create: `tests/test_history_router.py`

**Commit:** `feat: comment history page`

---

### Task F7: Rate Limit Banner + Remaining Quota `[ ]`

**Objective:** Dashboard tampil banner di atas Trending page: "Sisa quota komen: 3/5 dalam 6 jam terakhir. Reset jam 18:30". Kalau 0 → banner merah + Send button disabled.

**Files:**
- Modify: `server/routers/history.py` — `GET /history/quota` return `{used, limit, reset_at}`
- Modify: `dashboard/src/pages/Trending.tsx`

**Commit:** `feat: rate limit quota banner`

---

## Phase G — Polish & Deploy

### Task G1: Cookie Expired Banner `[ ]`

**Objective:** Kalau `fb_accounts.status == EXPIRED`, tampil banner merah di semua halaman + link ke re-connect.

**Commit:** `feat: cookie expired global banner`

---

### Task G2: Tests Pass Full Suite `[ ]`

**Server:**
```
pytest -q
# Expected: 320+ passed
```

**Commit:** (gak perlu kalau cuma run test)

---

### Task G3: Deploy `[ ]`

**Server steps (SSH):**
```
cd /home/ubuntu/fb-bot
git pull origin main
source venv/bin/activate
pip install -r requirements.txt  # kalau ada dep baru
alembic upgrade head
cd dashboard && npm install && npm run build && cd ..
sudo systemctl restart fb-bot-api fb-bot-worker fb-bot-beat
sudo systemctl status fb-bot-api fb-bot-worker fb-bot-beat
```

**Smoke test:**
- http://localhost:8100 200
- `/api/v1/health` 200
- `/api/v1/sources` 200 (empty list)
- `/api/v1/trending` 200 (empty list)
- `/api/v1/template` 200

**Commit:** `chore: activity log layer 1+2 deploy live`

---

## Current Next Step
**Phase A — Task A1: Migration.** Gue mau konfirmasi lu dulu sebelum mulai:

1. Jalan per-phase dengan review tiap fase selesai (A → B → C → ...), atau gue batch 2-3 fase sekaligus?
2. Ada perubahan scope/desain yang lu mau adjust sebelum gue mulai ngoding?

## Implementation Notes
_(kosong, nanti di-update setelah tiap task selesai)_

## Risk Register
- **Cookie expiry** — cookie FB realistic expire 7-30 hari. User harus siap re-connect.
- **FB ToS** — cookie-based scrape + auto comment (meskipun human-approved) tetep technically against ToS. Akun user risk kena review/lock. Rate limit MVP 5/6jam sengaja konservatif.
- **Playwright di server** — tambah ~300MB disk, butuh dep system (libs). Kalau gak ke-install bener, scanner semua fail.
- **FB UI berubah** — scraping HTML based, kalau FB ganti layout, parser bakal rusak. Perlu fallback/error handling.
- **No Meta App Review** — Layer 3 deferred, tapi Layer 1+2 jalan tanpa Meta API. MVP fine.
