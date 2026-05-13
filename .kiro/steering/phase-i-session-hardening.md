# Phase I — Session Hardening (Cookie Longevity)

> Living roadmap buat fix isu "cookie cepat expired". Update file ini tiap task
> kelar. Pairing sama `post-f5-roadmap.md` — phase ini nempel di §3 sebagai H-F
> replacement (H-F di-promote jadi fase aktif = Phase I).

**Baseline:** HEAD `ad29d3d` (2026-05-13, 638 test pass).
**Goal:** Extend cookie lifetime FB dari ~1-3 hari ke ~2-4 minggu dengan cara
bikin Playwright session keliatan konsisten & manusiawi di mata FB anti-bot.
**Target metric:** frekuensi `FBAccount.status → EXPIRED` turun min 60%.

---

## 0. Root Cause (Ringkas)

| # | Cause | Evidence | Fix Task |
|---|-------|----------|----------|
| 1 | Fingerprint berubah tiap run (UA default + viewport random per session) | `bot/modules/fb_session.py:106` `random.choice(_VIEWPORT_PRESETS)` | I-A |
| 2 | FB rotate `xs` cookie di `Set-Cookie`, kita gak simpan balik | `fb_account_service.replace_cookies` cuma dipanggil dari re-upload manual | I-B |
| 3 | Tiap run bikin context baru → `localStorage`, `IndexedDB`, `fb_dtsg` cache hilang → FB baca "browser baru" | `source_collector.py:301`, `comment_sender.py:387` pake `browser.new_context` | I-C |
| 4 | Scanner interval 900s (15 menit) dari VPS IP — rapid auth rhythm khas bot | `bot/celery_app.py:49` `SCAN_INTERVAL_SECONDS=900` | I-D |
| 5 | UA outdated (`Chrome/120`, Chrome real = 131+); `navigator.webdriver=true` tidak dipatch | `bot/modules/fb_session.py:37-40` | I-E |

**Effect combined:** FB ngeliat "cookie sama, browser tiap 15 menit ganti device"
→ anomaly score naik cepat → force re-auth / checkpoint.

---

## 1. Scope & Non-Goals

**In scope (Phase I):**
- Per-akun UA + viewport pinning (DB-backed, stable).
- Cookie rotation capture (save-back setelah session berhasil).
- Persistent browser profile per-akun via `launch_persistent_context` atau `storage_state` file.
- Scanner interval tuning + jitter.
- UA string refresh + `navigator.webdriver` patch sederhana (tanpa nambah dep playwright-stealth dulu — YAGNI).

**Out of scope (parked):**
- Residential/mobile proxy per-akun (Phase J — butuh budget + infra).
- `playwright-stealth` full plugin (evaluate after I-E effect keliatan).
- Multi-account rotation pool (H-F alt, masih parked).
- 2FA flow / TOTP handling.

---

## 2. Task Status Legend

- `[ ]` belum mulai
- `[~]` in progress / partial
- `[x]` done + committed + verified
- `[!]` blocked / deferred

---

## 3. Tasks

### Phase I-A — Per-Account Fingerprint Pinning

**Objective:** Simpan UA + viewport per-akun di DB. Reuse tiap session biar FB
ngeliat "device ini konsisten". Random cuma pas akun diciptakan, habis itu pin.

#### I-A-1 Migration + model field `browser_ua`, `viewport_w`, `viewport_h` `[x]` `e06186f`

Shipped 2026-05-13. Alembic rev `005_fb_fingerprint`, model `FBAccount` +3 fields,
test `test_create_fingerprint_fields_default_null` (RED `e8d4339` → GREEN `e06186f`).
Server migrated (004 → 005), 645/645 test pass.

**Files:**
- Modify: `server/models.py:154-191` (FBAccount class)
- Create: `alembic/versions/005_fb_fingerprint.py` (next rev after `004_scanner_runs`)
- Test: `tests/test_fb_account_service.py` (verif default null OK)

**Step 1: Append fields di FBAccount**

```python
# server/models.py, sebelum "class Source(Base):"
# Browser fingerprint — pinned per-account biar session looks stable di mata FB.
# Null untuk akun lama (pre-Phase-I); assign saat pertama kali dipake.
browser_ua: Mapped[str | None] = mapped_column(String(300), nullable=True)
viewport_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
viewport_h: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

**Step 2: Migration Alembic**

```python
# alembic/versions/005_fb_fingerprint.py
"""Add browser fingerprint fields to fb_accounts."""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("fb_accounts") as batch_op:
        batch_op.add_column(sa.Column("browser_ua", sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column("viewport_w", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("viewport_h", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("fb_accounts") as batch_op:
        batch_op.drop_column("viewport_h")
        batch_op.drop_column("viewport_w")
        batch_op.drop_column("browser_ua")
```

**Step 3: Run test create/read default null**

```bash
ssh rdpkhorur "cd /home/ubuntu/fb-bot && source venv/bin/activate && python -m pytest tests/test_fb_account_service.py -v -k create"
```
Expected: semua pass (default null gak break apa-apa).

**Step 4: Commit**

```bash
git add server/models.py server/migrations/003_fb_fingerprint.py
git commit -m "feat(i-a): add browser_ua/viewport fields to fb_accounts"
```

---

#### I-A-2 Service helper `ensure_fingerprint(account_id)` `[x]` `622fa2d`

Shipped 2026-05-13. Pool `bot/modules/fingerprint_pool.py` (3 UA Chrome 131/132 +
5 desktop viewports), service method `ensure_fingerprint` idempotent dengan
partial-null safe (fields NULL diisi tanpa overwrite yang sudah set). 4 test
pass. Commits: RED `c5d9b6c`, GREEN `622fa2d`.

**Objective:** Helper yang lazy-assign UA+viewport kalau akun belum punya.
Dipanggil dari scanner/sender sebelum create_session_context.

**Files:**
- Modify: `server/services/fb_account_service.py`
- Create: `bot/modules/fingerprint_pool.py` (constant pool UA + viewport buat pick random sekali)
- Test: `tests/test_fb_account_service.py` (3 case)

**Step 1: Write failing test**

```python
# tests/test_fb_account_service.py
class TestEnsureFingerprint:
    def test_assigns_when_null(self, db_session):
        acc = _mk_account(db_session, browser_ua=None, viewport_w=None, viewport_h=None)
        svc = FBAccountService(db_session)
        ua, w, h = svc.ensure_fingerprint(acc.id)
        assert ua and ua.startswith("Mozilla/5.0")
        assert w and h and w > 1000

    def test_idempotent_when_already_set(self, db_session):
        acc = _mk_account(
            db_session, browser_ua="UA-PINNED", viewport_w=1366, viewport_h=768,
        )
        svc = FBAccountService(db_session)
        ua, w, h = svc.ensure_fingerprint(acc.id)
        assert (ua, w, h) == ("UA-PINNED", 1366, 768)

    def test_raises_on_missing(self, db_session):
        svc = FBAccountService(db_session)
        with pytest.raises(ValueError, match="not found"):
            svc.ensure_fingerprint(99999)
```

**Step 2: Run — expected FAIL**

```bash
python -m pytest tests/test_fb_account_service.py::TestEnsureFingerprint -v
```

**Step 3: Implement fingerprint pool**

```python
# bot/modules/fingerprint_pool.py
"""Stable pool of browser fingerprints. Pick once per account, persist to DB.

Rationale: FB's anti-bot flags session hijacking when same cookie shows up
from different UA/viewport combos. Pinning per-account makes us look like a
single stable device.
"""
from __future__ import annotations
import random
from typing import Final

# Current-gen Chrome UAs (Chrome 130-132). Update yearly.
_UA_POOL: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

_VIEWPORT_POOL: Final = (
    (1366, 768),
    (1440, 900),
    (1536, 864),
    (1600, 900),
    (1920, 1080),
)


def pick_ua() -> str:
    return random.choice(_UA_POOL)


def pick_viewport() -> tuple[int, int]:
    return random.choice(_VIEWPORT_POOL)
```

**Step 4: Implement `ensure_fingerprint` di service**

```python
# server/services/fb_account_service.py (append method)
def ensure_fingerprint(self, account_id: int) -> tuple[str, int, int]:
    """Assign UA+viewport kalau akun belum punya. Return (ua, w, h)."""
    from bot.modules.fingerprint_pool import pick_ua, pick_viewport

    account = self.get_account(account_id)
    if not account:
        raise ValueError(f"FBAccount {account_id} not found")

    if account.browser_ua and account.viewport_w and account.viewport_h:
        return account.browser_ua, account.viewport_w, account.viewport_h

    ua = account.browser_ua or pick_ua()
    w, h = pick_viewport() if not account.viewport_w else (account.viewport_w, account.viewport_h)
    account.browser_ua = ua
    account.viewport_w = w
    account.viewport_h = h
    self.db.commit()
    return ua, w, h
```

**Step 5: Run tests — expect 3 pass**

**Step 6: Commit**

```bash
git add bot/modules/fingerprint_pool.py server/services/fb_account_service.py tests/test_fb_account_service.py
git commit -m "feat(i-a): ensure_fingerprint helper + UA/viewport pool"
```

---

#### I-A-3 Wire fingerprint ke `create_session_context` + refactor callers `[x]` `d66f604`

Shipped 2026-05-13. `scan_source` + `send_comment` signature add
`viewport=None`; `_run_scan_all_sources` + `send_comment` router call
`ensure_fingerprint(account.id)` sebelum invoke. `DEFAULT_USER_AGENT`
di-upgrade Chrome 120 → 131. 651/651 full suite pass. RED `c1e0b5a` → GREEN `d66f604`.

**Objective:** Caller (`source_collector`, `comment_sender`) panggil
`ensure_fingerprint` duluan, lalu pass UA+viewport ke `create_session_context`.

**Files:**
- Modify: `bot/modules/source_collector.py:300-305`
- Modify: `bot/modules/comment_sender.py:385-390`
- Modify: `bot/modules/fb_session.py:36-40` (update `DEFAULT_USER_AGENT` fallback ke Chrome 131)
- Test: existing scan/send tests harus tetap pass (signature tidak break — UA optional).

**Step 1: Update DEFAULT_USER_AGENT**

```python
# bot/modules/fb_session.py
DEFAULT_USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
```

**Step 2: Caller passes viewport**

Cari call site pakai `search_files("create_session_context", file_glob="*.py")`.
Di `source_collector.py:301`:

```python
# BEFORE
context = await create_session_context(
    browser, cookies, user_agent=user_agent
)

# AFTER — accept viewport dict kalau caller provide; callers passing scan()
# harus teruskan dari FBAccountService.ensure_fingerprint.
context = await create_session_context(
    browser, cookies, user_agent=user_agent, viewport=viewport,
)
```

Signature `scan_source(source, cookies, *, user_agent=None, viewport=None, max_posts=...)`.
Sama di `comment_sender.send_comment` — tambah `viewport: dict | None = None`.

**Step 3: Caller upstream (bot/tasks.py `scan_all_sources` + send task)**

Grep `scan_source(` dan `send_comment(` di `bot/tasks.py`, tambah step:
```python
ua, w, h = FBAccountService(db).ensure_fingerprint(account.id)
result = await scan_source(source, cookies, user_agent=ua, viewport={"width": w, "height": h})
```

**Step 4: Run full relevant suite**

```bash
ssh rdpkhorur "cd /home/ubuntu/fb-bot && source venv/bin/activate && python -m pytest tests/test_source_collector.py tests/test_comment_sender.py tests/test_tasks.py -v"
```

**Step 5: Commit**

```bash
git add bot/modules/fb_session.py bot/modules/source_collector.py bot/modules/comment_sender.py bot/tasks.py
git commit -m "feat(i-a): wire per-account UA+viewport into playwright context"
```

---

### Phase I-B — Cookie Rotation Capture

**Objective:** FB rotate session cookies (`xs`, `fr`, `datr`) kadang di response
`Set-Cookie`. Kita capture balik dari `context.cookies()` setelah session sukses,
replace yang kesimpen di DB. Ini yang bikin cookie "hidup terus".

#### I-B-1 Helper `capture_cookies_from_context(context) → dict` `[x]` `240e7da`

Shipped 2026-05-13. `bot/modules/fb_session.py` append helper yang filter cookie
domain ending `facebook.com` (catch leading-dot + bare subdomain) + tolerant ke
None/empty return. 5 test pass. Commits: RED `9a6d214`, GREEN `240e7da`.

#### I-B-2 Service method `refresh_cookies_silent(account_id, cookies)` `[x]` `86ec6b8`

Shipped 2026-05-13. `FBAccountService.refresh_cookies_silent` overwrites only
`cookies_encrypted`, rejects empty dict + missing `c_user`, never raises on
missing account. Paired stub fixture buat `encrypt_cookies`/`decrypt_cookies`.
6 test pass. Commits: RED `def6f42`, GREEN `86ec6b8`.

#### I-B-3 Wire capture di scanner + sender `[x]` `8b36028`

Shipped 2026-05-13. `scan_source` + `send_comment` accept
`on_cookies_refresh: Callable[[dict], Awaitable[None]] | None`, harvest via
`capture_cookies_from_context` right before context close, swallow callback
exceptions (best-effort). `_run_scan_all_sources` builds callback via
`_make_cookie_refresh_callback(db, account_id)`, trending `/comment` router
passes inline closure. 4 test (3 scanner + 1 orchestrator). Full suite **666
passed** (+15 since I-A). Deploy: `sudo systemctl restart fb-bot-api
fb-bot-worker fb-bot-beat` active, smoke via SessionLocal: `refresh_cookies_silent`
rotated `xs` → DB updated, status untouched, restore to original clean.
Commits: RED `8a02024`, GREEN `8b36028`.

**Files:**
- Modify: `bot/modules/fb_session.py` (append helper)
- Test: `tests/test_fb_session.py` (mock context.cookies())

**Step 1: Write failing test**

```python
# tests/test_fb_session.py
@pytest.mark.asyncio
async def test_capture_cookies_filters_and_dicts():
    fake_context = AsyncMock()
    fake_context.cookies.return_value = [
        {"name": "c_user", "value": "12345", "domain": ".facebook.com"},
        {"name": "xs", "value": "abc|def", "domain": ".facebook.com"},
        {"name": "junk", "value": "x", "domain": ".otherdomain.com"},
    ]
    out = await capture_cookies_from_context(fake_context)
    assert out == {"c_user": "12345", "xs": "abc|def"}
```

**Step 2: Implement**

```python
# bot/modules/fb_session.py (append)
async def capture_cookies_from_context(
    context: Any, *, domain_suffix: str = "facebook.com"
) -> dict[str, str]:
    """Extract current cookie state from context, keyed by name.

    FB rotate session cookies kadang di middle of session (new `xs` etc).
    Panggil ini setelah interaction sukses, lalu persist lewat
    FBAccountService.replace_cookies(..., keep_profile=True).
    """
    raw = await context.cookies()
    out: dict[str, str] = {}
    for c in raw or []:
        if domain_suffix not in (c.get("domain") or ""):
            continue
        out[c["name"]] = c["value"]
    return out
```

**Step 3: Run — expect 1 pass**

**Step 4: Commit**

```bash
git add bot/modules/fb_session.py tests/test_fb_session.py
git commit -m "feat(i-b): add capture_cookies_from_context helper"
```

---

#### I-B-2 Service method `refresh_cookies_silent(account_id, cookies)`

**Objective:** Variant dari `replace_cookies` yang cuma update encrypted cookie,
TANPA touch profile fields (fb_name, status, failure_count). Dipake buat
rotation silent — kita gak mau flip status ACTIVE tiap rotate.

**Files:**
- Modify: `server/services/fb_account_service.py`
- Test: `tests/test_fb_account_service.py`

**Step 1: Write failing test**

```python
# tests/test_fb_account_service.py
class TestRefreshCookiesSilent:
    def test_overwrites_encrypted_only(self, db_session):
        acc = _mk_account_with_cookies(db_session, cookies={"c_user": "old"})
        svc = FBAccountService(db_session)
        svc.refresh_cookies_silent(acc.id, cookies={"c_user": "NEW", "xs": "ROT"})
        fresh = svc.decrypt_cookies(svc.get_account(acc.id))
        assert fresh == {"c_user": "NEW", "xs": "ROT"}

    def test_does_not_touch_status_or_profile(self, db_session):
        acc = _mk_account_with_cookies(
            db_session, cookies={"c_user": "old"}, status="ACTIVE", fb_name="Foo",
        )
        svc = FBAccountService(db_session)
        svc.refresh_cookies_silent(acc.id, cookies={"c_user": "NEW"})
        after = svc.get_account(acc.id)
        assert after.status == "ACTIVE"
        assert after.fb_name == "Foo"
        assert after.failure_count == 0
```

**Step 2: Implement**

```python
# server/services/fb_account_service.py
def refresh_cookies_silent(
    self, account_id: int, *, cookies: dict[str, str]
) -> None:
    """Rewrite encrypted cookies tanpa touch profile/status. Buat I-B rotation capture."""
    if not cookies or "c_user" not in cookies:
        # Defensive — jangan overwrite dengan state invalid.
        return
    account = self.get_account(account_id)
    if not account:
        return
    account.cookies_encrypted = self._encrypt_cookies(cookies)
    self.db.commit()
```

**Step 3: Run tests expect pass.**

**Step 4: Commit**

```bash
git add server/services/fb_account_service.py tests/test_fb_account_service.py
git commit -m "feat(i-b): refresh_cookies_silent for rotation capture"
```

---

#### I-B-3 Wire capture di `source_collector` + `comment_sender` finally block

**Objective:** Setelah session sukses (scan / send) dan sebelum context close,
capture cookies + silent-refresh DB.

**Files:**
- Modify: `bot/modules/source_collector.py` (after scroll loop, before context close)
- Modify: `bot/modules/comment_sender.py` (after send confirmed)
- Modify: `bot/tasks.py` (pass db ref + account_id ke scan/send)

Option A — caller holds the svc, passes callback:

```python
# bot/modules/source_collector.py signature
async def scan_source(
    source, cookies, *, user_agent=None, viewport=None, max_posts=...,
    on_cookies_refresh: Callable[[dict[str, str]], Awaitable[None]] | None = None,
):
    ...
    # inside try, after successful scroll loop
    if on_cookies_refresh:
        fresh = await capture_cookies_from_context(context)
        if fresh:
            await on_cookies_refresh(fresh)
```

Caller (`bot/tasks.py scan_all_sources`):

```python
async def _refresh(new_cookies: dict[str, str]) -> None:
    FBAccountService(db).refresh_cookies_silent(account.id, cookies=new_cookies)

await scan_source(source, cookies, user_agent=ua, viewport=vp, on_cookies_refresh=_refresh)
```

**Step 1: Extend test**

Tambah test `TestScanSourceCookieRotation` — mock context.cookies return rotated
value, assert callback dipanggil dengan dict itu.

**Step 2: Implement per-caller.**

**Step 3: Full suite**

```bash
ssh rdpkhorur "... && python -m pytest tests/ -v --tb=short"
```

**Step 4: Commit**

```bash
git add bot/modules/source_collector.py bot/modules/comment_sender.py bot/tasks.py tests/
git commit -m "feat(i-b): capture rotated cookies after scan/send and persist"
```

---

### Phase I-C — Persistent Browser Profile

**Objective:** Replace `browser.new_context()` pattern dengan
`chromium.launch_persistent_context(user_data_dir)` per-akun. Efeknya
`localStorage`, `IndexedDB`, service worker cache, dan `fb_dtsg` token persist
across runs → FB ngeliat 1 browser konsisten.

#### I-C-1 Disk path helper + cleanup policy

**Files:**
- Create: `bot/modules/browser_profile.py`
- Test: `tests/test_browser_profile.py`

**Step 1: Test**

```python
def test_profile_path_per_account(tmp_path, monkeypatch):
    monkeypatch.setenv("FB_PROFILE_ROOT", str(tmp_path))
    p = get_profile_path(account_id=42)
    assert p.parent == tmp_path
    assert p.name == "account-42"
    p.mkdir(parents=True, exist_ok=True)
    assert p.exists()


def test_profile_path_default_root(monkeypatch, tmp_path):
    monkeypatch.delenv("FB_PROFILE_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    p = get_profile_path(1)
    assert "fb-profiles" in str(p)
```

**Step 2: Implement**

```python
# bot/modules/browser_profile.py
"""Per-account Playwright user_data_dir paths.

Layout:
    $FB_PROFILE_ROOT/account-<id>/         (persistent Chromium profile dir)

Default FB_PROFILE_ROOT = $HOME/.fb-bot/fb-profiles
"""
from __future__ import annotations
import os
from pathlib import Path


def get_profile_root() -> Path:
    override = os.getenv("FB_PROFILE_ROOT")
    if override:
        return Path(override)
    return Path.home() / ".fb-bot" / "fb-profiles"


def get_profile_path(account_id: int) -> Path:
    return get_profile_root() / f"account-{account_id}"
```

**Step 3: Run test expect pass.**

**Step 4: Commit**

```bash
git add bot/modules/browser_profile.py tests/test_browser_profile.py
git commit -m "feat(i-c): browser profile path helper"
```

---

#### I-C-2 New `create_persistent_session(account_id, cookies, ua, viewport)`

**Objective:** Replacement buat `create_session_context` yang pake
`launch_persistent_context`. Return (context, page-ready).

**Files:**
- Modify: `bot/modules/fb_session.py`
- Test: `tests/test_fb_session.py` (mock playwright, assert launch_persistent_context called w/ correct args)

**Step 1: Test**

```python
@pytest.mark.asyncio
async def test_create_persistent_session_uses_profile_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FB_PROFILE_ROOT", str(tmp_path))

    fake_pw = MagicMock()
    fake_ctx = AsyncMock()
    fake_pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_ctx)

    ctx = await create_persistent_session(
        fake_pw, account_id=7, cookies={"c_user": "1"},
        user_agent="UA", viewport={"width": 1366, "height": 768},
    )
    fake_pw.chromium.launch_persistent_context.assert_awaited_once()
    args, kwargs = fake_pw.chromium.launch_persistent_context.call_args
    assert str(tmp_path / "account-7") in args[0] or kwargs.get("user_data_dir")
    assert kwargs["user_agent"] == "UA"
    assert kwargs["viewport"] == {"width": 1366, "height": 768}
```

**Step 2: Implement**

```python
# bot/modules/fb_session.py (append)
from bot.modules.browser_profile import get_profile_path


async def create_persistent_session(
    playwright: Any,
    *,
    account_id: int,
    cookies: dict[str, str],
    user_agent: str | None = None,
    viewport: dict[str, int] | None = None,
    locale: str = "id-ID",
    timezone_id: str = "Asia/Jakarta",
    headless: bool = True,
) -> Any:
    """Launch persistent Chromium context scoped to `account_id`.

    Unlike `create_session_context`, this persists localStorage, IndexedDB,
    service workers, and rotated cookies across runs — crucial for FB
    anti-bot to see a stable device fingerprint.
    """
    profile_dir = get_profile_path(account_id)
    profile_dir.mkdir(parents=True, exist_ok=True)

    ua = user_agent or DEFAULT_USER_AGENT
    vp = viewport or random.choice(_VIEWPORT_PRESETS)

    context = await playwright.chromium.launch_persistent_context(
        str(profile_dir),
        headless=headless,
        user_agent=ua,
        viewport=vp,
        locale=locale,
        timezone_id=timezone_id,
    )
    # Cookies may already be in the profile; re-apply provided dict to be safe.
    await context.add_cookies(cookies_dict_to_playwright_format(cookies))
    return context
```

**Step 3: Run test pass.**

**Step 4: Commit**

```bash
git add bot/modules/fb_session.py tests/test_fb_session.py
git commit -m "feat(i-c): create_persistent_session using launch_persistent_context"
```

---

#### I-C-3 Swap callers dari `create_session_context` ke `create_persistent_session`

**Files:**
- Modify: `bot/modules/source_collector.py:297-304` (`async with async_playwright()` block)
- Modify: `bot/modules/comment_sender.py:383-390`

Pattern change — persistent context digabung sama browser lifecycle:

```python
# BEFORE
async with async_playwright() as pw:
    browser = await pw.chromium.launch(headless=True)
    context = await create_session_context(browser, cookies, user_agent=user_agent, viewport=viewport)
    page = await context.new_page()
    ...

# AFTER
async with async_playwright() as pw:
    context = await create_persistent_session(
        pw, account_id=account_id, cookies=cookies,
        user_agent=user_agent, viewport=viewport, headless=True,
    )
    try:
        page = await context.new_page()
        ...
    finally:
        await context.close()
```

`account_id` harus di-thread dari caller (scan_all_sources + send_comment task).
Scanner/sender signature add `account_id: int` param.

**Step 1: Update signatures + upstream**

**Step 2: Full suite**

```bash
ssh rdpkhorur "... && python -m pytest tests/ -v"
```

**Step 3: Commit**

```bash
git add bot/modules/source_collector.py bot/modules/comment_sender.py bot/tasks.py tests/
git commit -m "feat(i-c): switch scan/send to persistent browser profile per-account"
```

---

#### I-C-4 Add `server/routers/fb_accounts.py :: DELETE /{id}` → hook to nuke profile dir

**Objective:** Kalau akun di-delete, hapus `~/.fb-bot/fb-profiles/account-<id>/`
biar gak jadi zombie data.

**Files:**
- Modify: `server/routers/fb_accounts.py` (existing delete endpoint)
- Test: `tests/test_fb_accounts_router.py`

**Step 1: Test**

```python
def test_delete_account_removes_profile_dir(client, admin_token, tmp_path, monkeypatch):
    monkeypatch.setenv("FB_PROFILE_ROOT", str(tmp_path))
    pdir = tmp_path / "account-99"
    pdir.mkdir()
    (pdir / "sentinel").write_text("x")
    # Create account with id=99 first, then delete
    ...
    resp = client.delete("/api/v1/fb-accounts/99", headers=admin_token)
    assert resp.status_code == 200
    assert not pdir.exists()
```

**Step 2: Implement** (add `shutil.rmtree(get_profile_path(id), ignore_errors=True)` before db delete).

**Step 3: Commit**

```bash
git commit -m "feat(i-c): cleanup browser profile on fb-account delete"
```

---

### Phase I-D — Scanner Rate Limit + Jitter

**Objective:** Kurangin frekuensi scan dari 15 min → 25-35 min w/ jitter.
Hilangin "beat yg terlalu robotik".

#### I-D-1 Tune `SCAN_INTERVAL_SECONDS` default + add jitter `[x]` `3777354`

Shipped 2026-05-13. `bot/celery_app.py _scan_interval()` default bumped
900 → 1800 (30 min). `bot/tasks.py` +2 helpers `_sleep_startup_jitter()`
(0-120s random) dan `_sleep_inter_source()` (30-90s random) broken out
biar gampang di-monkeypatch di test. `_scan_enabled_sources` wires
jitter di awal loop + think-time `if idx > 0`. 5 new test
(test_celery_schedule 3x + test_scan_all_sources 2x) — autouse fixture
`_no_scanner_sleep` no-op cadence buat existing tests (suite tetap fast).
Full suite **671 passed** (+5 sejak I-B). Deploy: `sudo systemctl
restart fb-bot-api fb-bot-worker fb-bot-beat` all active, beat reloaded
clean, smoke verify `_scan_interval()==1800`, `_STARTUP_JITTER_MAX=120`,
`_INTER_SOURCE_DELAY=30-90`. Commits: RED `9a1983f`, GREEN `3777354`,
test-speedup `1db3561`.

**Files:**
- Modify: `bot/celery_app.py:48-49`
- Modify: `bot/tasks.py` (inside `scan_all_sources` add random sleep at start)
- Test: `tests/test_celery_config.py` (new)

**Step 1: Test**

```python
def test_scan_interval_default_raised(monkeypatch):
    monkeypatch.delenv("SCAN_INTERVAL_SECONDS", raising=False)
    from bot.celery_app import _scan_interval
    assert _scan_interval() >= 1500  # >= 25 min
```

**Step 2: Bump default + jitter helper**

```python
# bot/celery_app.py
def _scan_interval() -> int:
    return int(os.getenv("SCAN_INTERVAL_SECONDS", "1800"))  # 30 min default
```

```python
# bot/tasks.py  scan_all_sources entry
import random
jitter = random.uniform(0, 120)  # 0-2 min jitter
logger.info("scan_all_sources sleeping jitter=%.1fs", jitter)
await asyncio.sleep(jitter)
```

**Step 3: Inter-source delay sudah ada (`_SCROLL_DELAY_MIN/MAX`) — tambah delay antar source** (scanner loop) kalau scan >1 source:

```python
for source in sources:
    await scan_source(...)
    await asyncio.sleep(random.uniform(30, 90))  # think-time
```

**Step 4: Commit**

```bash
git commit -m "feat(i-d): raise scan interval to 30min + jitter + inter-source think-time"
```

---

#### I-D-2 Document env override

**Files:** `docs/DEPLOY.md`

```markdown
### Scan rhythm (Phase I)

Default `SCAN_INTERVAL_SECONDS=1800` (30 min). Jangan turunin <1500 — FB
anti-bot trigger lebih gampang di rapid auth rhythm.
```

Commit: `docs(i-d): note new scan interval guidance`.

---

### Phase I-E — Stealth: WebDriver Patch + UA Upgrade

**Objective:** Patch `navigator.webdriver=false` + hide `HeadlessChrome`
markers. Cheap win sebelum (potentially) adopt playwright-stealth full.

#### I-E-1 Inject `navigator.webdriver` override via `add_init_script` `[x]` `2a00bc7`

Shipped 2026-05-13. `STEALTH_INIT_SCRIPT` di `bot/modules/fb_session.py:42-71`
patch 4 marker (`navigator.webdriver`, `plugins`, `languages`, `window.chrome`),
attached via `context.add_init_script(...)` sebelum `add_cookies` (before first
nav) di `create_session_context`. 6 new test (RED `ed0933e` → GREEN `2a00bc7`
→ test fixture backfill `948858c`, `f3d2113`). Full suite **677 passed** di
server. Services active `f3d2113`, smoke: 4 occurrence stealth refs di file.

**Files:**
- Modify: `bot/modules/fb_session.py` — di `create_persistent_session` sebelum return, panggil `context.add_init_script(...)` w/ patch.

```python
_STEALTH_PATCH = """
Object.defineProperty(navigator, 'webdriver', { get: () => false });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['id-ID', 'id', 'en-US', 'en'] });
window.chrome = window.chrome || { runtime: {} };
"""

await context.add_init_script(_STEALTH_PATCH)
```

**Step 1: Test** — new page, `page.evaluate("navigator.webdriver")` → `False`.
(Skip kalau playwright test infra heavy; acceptable manual smoke.)

**Step 2: Commit**

```bash
git commit -m "feat(i-e): patch navigator.webdriver + plugins via init script"
```

---

## 4. Verification Plan

Setelah semua phase I-A…I-E merged:

1. **Unit:** `pytest tests/ -v` — target 650+ pass (638 baseline + ~15 new).
2. **Lint/typecheck:** sesuai skrip project (cek `search_files("ruff|mypy|pre-commit", file_glob="*.toml")`).
3. **Build dashboard:** `cd dashboard && npm run build` (Phase I is backend-only, tapi trigger buat confirm no accidental FE regression).
4. **Deploy to rdpkhorur:** git pull + restart `fb-bot-api`, `fb-bot-worker`, `fb-bot-beat` (sudo — butuh approval user).
5. **Manual smoke (user-driven):**
   - Live akun masih ACTIVE setelah deploy.
   - 2-3 jam kemudian — scanner udah run 3-4x, akun belum flip EXPIRED.
   - Cek `~/.fb-bot/fb-profiles/account-<id>/` di server — ada isi.
   - Cek `fb_accounts.browser_ua` di DB — ke-populate.
6. **Week-scale metric:** ambil baseline "hari-ke-expired" sebelum I (dari log/DB), compare 1-2 minggu setelah I.

---

## 5. Rollout Order

Wajib merge per-phase, deploy, lihat efek 24 jam, baru lanjut:

```
I-A (fingerprint pin) → deploy → 24h observe
I-B (cookie rotation) → deploy → 24h observe
I-C (persistent profile) → deploy → 24h observe
I-D (scan interval)     → deploy → 7-day observe
I-E (stealth patch)     → deploy → optional
```

Reason: kalau satu phase bikin regression (e.g. I-C breaks profile lock),
gampang bisect.

---

## 6. Parking Lot (Phase J+)

- **Residential proxy per-akun** — pakai service kayak Bright Data / Soax. Pin IP geo ke Indonesia. Effort tinggi (budget + config). Ada kalau I-A..I-E belum cukup.
- **playwright-stealth full plugin** — replace I-E manual patch kalau masih sering ketangkep.
- **Mobile emulation** — launch sebagai Android Chrome via device descriptor. Alternative fingerprint kalau desktop flagged.
- **Warmup routine** — sebelum goto target URL, visit `/`, idle 3-5s, scroll, baru ke target. Lebih human-like.
- **Cookie TTL monitor dashboard** — chart "days-alive" per akun di UI. Jadi kita bisa liat impact Phase I quantitatively.

---

## 7. How to Resume

1. Baca file ini (`.kiro/steering/phase-i-session-hardening.md`) dulu.
2. Pilih task `[ ]` terdekat dari §3 — urutan strict (I-A-1 → I-A-2 → ...).
3. TDD: RED (failing test) → GREEN (implement) → commit.
4. Update status task dengan `[x]` + SHA commit.
5. Habis tiap phase utama (A/B/C/D/E), update `development-behavior.md` activity log + `post-f5-roadmap.md` (update §3.6 H-F status ke "superseded by Phase I").

**Current Next Step:** I-A-1 — add `browser_ua`/`viewport_w`/`viewport_h` columns ke `fb_accounts` + migration.
