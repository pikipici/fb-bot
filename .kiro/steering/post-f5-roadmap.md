# Post-F5 Roadmap — FB Bot

> Living roadmap yang nge-capture semua kerja sejak F5 (send endpoint + UI wiring)
> plus rencana F7 + Phase H ke depan. Update file ini tiap phase kelar biar
> konteks tetap segar.

**Baseline:** F5 selesai di HEAD `45eda50` (2026-05-10, 566 test pass).
**State aktual:** HEAD `029144e` (2026-05-12).
**Format:** shipped = retrospective ringkas + commit anchor; pending = bite-sized tasks TDD-friendly.

---

## 1. Shipped Sejak F5 (Retrospective)

Ini semua yang udah merged + live tapi belum ke-log di `development-behavior.md`.
Anggep ini "activity log extension" sampe log aslinya di-trim & di-refresh.

### 1.1 Trending UX polish
| SHA | Ringkas |
|-----|---------|
| `7bcaf6d` | Reject stories/reel/watch URL dengan 415 + UI badge warning saat post jenis itu ke-scrape. |
| `bcdd9df` | Drop stories/reel/watch URL di upsert layer via shared `fb_url` util. |
| `c107c1a` | Multi-locale composer selector (EN + ID) di comment sender. |
| `029144e` | Rename badge `Baru` → `Belum komen`; tambah `Fresh` badge via `localStorage.lastSeenAt` di Trending. |

### 1.2 F6 — Comment History UI + audit (DONE)
| SHA | Ringkas |
|-----|---------|
| `d82ed4d` | `GET /api/v1/history` list endpoint (any auth role). Query `status=SENT\|FAILED\|PENDING`, `limit` (default 50, max 200), `offset`. Response envelope `{items, total}` dengan post summary embed (author, snippet, url, thumbnail, status). 400 invalid status. Router test lengkap. |
| `316cf17` | Dashboard page `/history` + nav link. Pagination client-side pake `limit`/`offset` (page size 25). Filter status (`ALL\|SENT\|FAILED\|PENDING`). Tiap row tampilin post summary + comment text + error message + timestamp + fb_comment_id kalau ada. |

File: `server/routers/history.py`, `dashboard/src/pages/History.tsx`.

### 1.3 Phase G — Hardening & UX (DONE)
| SHA | Ringkas |
|-----|---------|
| `e653106` | `ErrorBoundary` app-wide (`dashboard/src/components/error-boundary.tsx`) + global React Query error toast via `queryCache.onError` + `mutationCache.onError`. Ignore `Unauthorized` (biar gak double-toast login redirect). |
| `a3e8459` | Rewrite `README.md`, tambah `docs/ARCHITECTURE.md` + `docs/DEPLOY.md`. |
| `9a7d808` | Code-split per-route via `lazy(() => import(...))` + global quota widget (`QuotaWidget` di header) + dark mode default. Initial JS bundle shrink significant. |
| `f94333d` | Cleanup — remove unused Review Queue page + drafts/approvals routers (warisan fase-1/fase-2 yang udah obsolete). |

### 1.4 AI Draft Integration (off-plan, DONE)
| SHA | Ringkas |
|-----|---------|
| `e33a1ac` | `AIDraftService` (OpenAI-compatible, default endpoint `https://ai.sumopod.com/v1`, model `MiniMax-M2.7-highspeed`). In-memory per-user cooldown 15s. Env: `SUMOPOD_API_KEY`, `SUMOPOD_BASE_URL`, `AI_DRAFT_MODEL`. Template aktif dipake sebagai *style reference*, bukan verbatim. |
| `71b13c4`, `fe1fa17`, `8b81c31` | Test fixture + rate-limit reset + AsyncMock patch `_call_llm` (bukan `httpx.Client` — bentrok TestClient loop). |
| `85eb01c` | Router `POST /api/v1/trending/{id}/ai-draft` (admin-only). Flip status ke `DRAFTED`, rate-limit 429. |
| `d7e2ca7` | UI sparkle wand button di Trending card. Dirty-check: kalau user udah edit draft manual, confirm sebelum overwrite. |

### 1.5 Scanner Audit + UX (off-plan, DONE)
| SHA | Ringkas |
|-----|---------|
| `ddb35d2` | `ScannerRun` audit table + `GET /api/v1/scanner/status` (last run, duration, inserted/updated/skipped) + `POST /api/v1/scanner/run-now` (admin). |
| `9505c4b` | Normalize tz-naive `started_at` dari SQLite; accept 401/403 di auth test. |
| `c3e1213` | Replace misleading "last fetch" timestamp di UI dengan `ScannerIndicator` component (dot color by fresh/stale) + admin "Scan now" button. |
| `e2ba6c8` | Fix bug 7-jam: serialize timestamp sebagai UTC-explicit ISO (`+00:00` suffix) biar browser lokal-timezone gak kebaca salah. |

### 1.6 Cookie/Login Health Surfacing (off-plan, DONE)
| SHA | Ringkas |
|-----|---------|
| `f2d69a9` | `bot/modules/fb_auth_probe.py` — DOM-based login-wall detection (bukan cuma URL check). |
| `00f6b9e` | Sender raise `CookieExpiredError` saat probe detect login wall, bahkan kalau URL keliatan normal. |
| `34cc430` | Scanner (`source_collector`) juga pake probe → `CookieExpiredError` → auto-mark `FBAccount.status=EXPIRED`. |
| `2f31152` | Global `AccountStatusBanner` di App shell — red bar kalau FBAccount `EXPIRED` atau `BLOCKED`, admin dapet tombol shortcut `Buka Accounts`. Poll tiap 60s + refetch on focus. |

### 1.7 Photo Viewer Composer Fix (off-plan, DONE)
| SHA | Ringkas |
|-----|---------|
| `ac488ea` | Scroll-to-bottom buat hydrate composer di photo viewer + locale `Berkomentar` (ID). |
| `74947b0` | Text-based fallback buat expand stub button `Tulis komentar` kalau `Leave a comment` (EN) gak ketemu. |
| `3cbd606`, `106c592`, `16d780b`, `e458d37`, `263122f` | Chore probes buat debug + validate modal photo viewer DOM. |

---

## 2. Next — F7: Account Recovery UX (DONE 2026-05-12)

Shipped di HEAD `d976df3`. Task status:

- [x] F7-1 surface CHECKPOINT di banner + FBAccounts card (`ce0b17a`).
- [x] F7-2 `POST /fb-accounts/{id}/re-validate` + 5 router test (`0867b3a` → fix `f620b0f` + `e5d35e8`).
- [x] F7-3 `POST /fb-accounts/{id}/re-upload-cookie` + 6 router test (`3e4bed4`).
- [x] F7-4 FE helpers `api.reValidateFBAccount` + `api.reUploadFBCookie` (`d976df3`).
- [x] F7-5 Re-validate button di card (cookie-only akun).
- [x] F7-6 Re-upload Cookie dialog + preview + confirm.
- [x] F7-7 Deep-link `/accounts?action=reupload` dari AccountStatusBanner.
- [x] F7-8 Verify + deploy: full suite **638 passed**, dashboard build 2.76s, service restart active, smoke `POST /1/re-validate` 200 `valid=false`, unknown 404 ✓.

Pitfall yang ketemu saat jalan:
- Test fixture lama `create_all` dipanggil sebelum `server.main` ter-import → `Base.metadata` belum kenal `User` → "no such table: users" saat run class isolasi. Fix: pre-import `server.main` di top-level test module.
- Mid-test `SessionLocal()` write bentrok sama TestClient connection di SQLite. Fix: pakai existing API `PUT /fb-accounts/{id}` buat seed state, bukan manual session.

Actionable item berikutnya: pilih salah satu dari §3.

### 2.1 Kondisi Saat Ini (gap)

Sekarang saat cookie expired / checkpoint:
1. Scanner/sender auto-flip `FBAccount.status` → `EXPIRED` atau `CHECKPOINT` (backend OK).
2. `AccountStatusBanner` cuma handle `EXPIRED` + `BLOCKED` — **CHECKPOINT tidak di-surface**.
3. Di `/accounts` page: tombol `Reactivate` cuma muncul kalau status `BLOCKED`. Untuk `EXPIRED`/`CHECKPOINT`, user harus **hapus akun dulu baru connect ulang** (karena single-account invariant di `POST /connect-cookie` return 409 kalau ada akun).
4. Gak ada **re-validate** button — user gak bisa test cookie lama masih jalan tanpa kirim real komen.
5. `cookies_expired_at` ke-set di service tapi belum dipakai di UI (badge "cookie lu expired 3 jam lalu").

User flow ideal: pas banner nongol EXPIRED → klik `Buka Accounts` → klik `Re-upload Cookie` → paste cookie baru → preview → confirm → akun balik ACTIVE, banner auto-hide.

### 2.2 Tasks

#### F7-1: Surface CHECKPOINT di AccountStatusBanner + FBAccounts page

**Objective:** Tambahin status `CHECKPOINT` ke list status yang banner dan per-card warning handle.

**Files:**
- Modify: `dashboard/src/components/account-status-banner.tsx` (line 41 — status filter)
- Modify: `dashboard/src/pages/FBAccounts.tsx` (line ~95-102 — `statusBadgeVariant` + line 399-416 warning block)
- Test: (manual — tidak ada FE test suite yet)

**Step 1: Banner extend to CHECKPOINT**

```tsx
// account-status-banner.tsx
const status = account.status as string
if (status !== 'EXPIRED' && status !== 'BLOCKED' && status !== 'CHECKPOINT') return null

const message =
  status === 'EXPIRED'
    ? 'Cookie FB lu expired — scanner & send gak bakal jalan sampai di-reconnect.'
    : status === 'CHECKPOINT'
    ? 'FB minta checkpoint (verifikasi tambahan). Selesaikan di browser dulu, lalu re-upload cookie baru.'
    : 'Akun FB terblokir oleh Facebook — scanner & send ditahan.'
```

**Step 2: FBAccounts page — `statusBadgeVariant['CHECKPOINT'] = 'warning'`, warning block tambahan.**

**Step 3: Commit**

```bash
git add dashboard/src/components/account-status-banner.tsx dashboard/src/pages/FBAccounts.tsx
git commit -m "feat(f7): surface CHECKPOINT account status in banner + FBAccounts page"
```

---

#### F7-2: Backend — `POST /api/v1/fb-accounts/{id}/re-validate`

**Objective:** Endpoint admin yang decrypt cookie existing, jalanin `validate_and_fetch_profile`, flip status sesuai hasil (ACTIVE kalau valid, EXPIRED kalau 401/redirect login). Tidak mutate cookie, cuma re-test.

**Files:**
- Modify: `server/routers/fb_accounts.py` (tambah handler `re_validate_account`)
- Modify: `server/services/fb_account_service.py` (helper `mark_active(account_id)` kalau belum ada)
- Test: `tests/test_fb_accounts_router.py` (tambah 3 test case)

**Step 1: Write failing test**

```python
# tests/test_fb_accounts_router.py

@pytest.mark.asyncio
async def test_revalidate_account_success_marks_active(
    client, admin_token, expired_cookie_account, monkeypatch
):
    async def fake_validate(_cookies):
        return CookieProfile(fb_user_id="123", name="X", profile_pic_url=None)
    monkeypatch.setattr(
        "server.routers.fb_accounts.validate_and_fetch_profile", fake_validate
    )
    res = client.post(
        f"/api/v1/fb-accounts/{expired_cookie_account.id}/re-validate",
        headers=admin_token,
    )
    assert res.status_code == 200
    assert res.json()["account"]["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_revalidate_account_still_expired_keeps_expired(
    client, admin_token, expired_cookie_account, monkeypatch
):
    async def fake_validate(_cookies):
        raise CookieValidationError("login wall")
    monkeypatch.setattr(
        "server.routers.fb_accounts.validate_and_fetch_profile", fake_validate
    )
    res = client.post(
        f"/api/v1/fb-accounts/{expired_cookie_account.id}/re-validate",
        headers=admin_token,
    )
    assert res.status_code == 200
    assert res.json()["account"]["status"] == "EXPIRED"
    assert res.json()["valid"] is False


def test_revalidate_account_without_cookies_returns_400(
    client, admin_token, manual_account
):
    res = client.post(
        f"/api/v1/fb-accounts/{manual_account.id}/re-validate",
        headers=admin_token,
    )
    assert res.status_code == 400
```

**Step 2: Run test to verify failure**
`pytest tests/test_fb_accounts_router.py::test_revalidate_account_success_marks_active -v`
Expected: FAIL (endpoint 404).

**Step 3: Implement handler**

```python
# server/routers/fb_accounts.py (append)
@router.post("/{account_id}/re-validate")
async def re_validate_account(
    account_id: int,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    """Test apakah cookie yang tersimpan masih valid, tanpa mengubah cookie.

    - 400 kalau akun bukan cookie-based (gak ada cookies_encrypted).
    - 404 kalau akun gak ada.
    - 200 {account, valid} kalau proses selesai, valid=true/false.
    """
    svc = FBAccountService(db)
    account = svc.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    if not account.cookies_encrypted:
        raise HTTPException(400, "Akun ini manual, gak punya cookie untuk divalidasi")

    cookies = svc.decrypt_cookies(account)
    try:
        profile = await validate_and_fetch_profile(cookies)
    except CookieValidationError:
        svc.mark_expired(account.id)
        refreshed = svc.get_account(account_id)
        return {"valid": False, "account": svc.to_dict(refreshed, include_email=True)}

    svc.mark_active_from_profile(
        account.id,
        fb_user_id=profile.fb_user_id,
        fb_name=profile.name,
        fb_profile_pic_url=profile.profile_pic_url,
    )
    refreshed = svc.get_account(account_id)
    return {"valid": True, "account": svc.to_dict(refreshed, include_email=True)}
```

**Step 4: Run tests, expect 3 passed.**

**Step 5: Commit**

```bash
git add server/routers/fb_accounts.py server/services/fb_account_service.py tests/test_fb_accounts_router.py
git commit -m "feat(f7): add POST /fb-accounts/{id}/re-validate endpoint"
```

---

#### F7-3: Backend — `POST /api/v1/fb-accounts/{id}/re-upload-cookie`

**Objective:** Endpoint admin buat replace cookie tanpa harus delete+recreate (biar `id`, `label`, `notes`, `total_uses` history tetap). Enforce preview-before-save.

**Files:**
- Modify: `server/routers/fb_accounts.py`
- Modify: `server/services/fb_account_service.py` (helper `replace_cookies(account_id, cookies, fb_user_id, fb_name, fb_profile_pic_url)`)
- Test: `tests/test_fb_accounts_router.py` (5 test case)

**Step 1: Write failing test (5 cases)**
- ✅ happy path: new cookie valid → status ACTIVE, `cookies_expired_at` cleared, profile updated, 200 response.
- ❌ new cookie invalid (CookieValidationError) → 400, tidak overwrite.
- ❌ account tidak ada → 404.
- ❌ `raw_cookies` kosong → 400.
- ❌ akun manual (no `cookies_encrypted`) → 400 "akun ini manual".

**Step 2: Implement**

```python
class ReUploadCookieRequest(BaseModel):
    raw_cookies: str


@router.post("/{account_id}/re-upload-cookie")
async def re_upload_cookie(
    account_id: int,
    req: ReUploadCookieRequest,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    """Replace cookie pada akun existing. Label, notes, history tetap."""
    if not req.raw_cookies or not req.raw_cookies.strip():
        raise HTTPException(400, "raw_cookies kosong")

    svc = FBAccountService(db)
    account = svc.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    if not account.cookies_encrypted:
        raise HTTPException(400, "Akun ini manual, pakai endpoint update biasa")

    cookies = parse_cookie_string(req.raw_cookies)
    try:
        profile = await validate_and_fetch_profile(cookies)
    except CookieValidationError as exc:
        raise HTTPException(400, str(exc)) from exc

    svc.replace_cookies(
        account.id,
        cookies=cookies,
        fb_user_id=profile.fb_user_id,
        fb_name=profile.name,
        fb_profile_pic_url=profile.profile_pic_url,
    )
    refreshed = svc.get_account(account_id)
    return {"account": svc.to_dict(refreshed, include_email=True)}
```

`FBAccountService.replace_cookies` must:
- Encrypt new cookies via Fernet.
- Set `cookies_encrypted`, `fb_user_id`, `fb_name`, `fb_profile_pic_url`.
- Clear `cookies_expired_at`.
- Set `status = 'ACTIVE'`, `failure_count = 0`.
- Commit.

**Step 3: Run tests, expect 5 passed.**

**Step 4: Commit**

```bash
git add server/routers/fb_accounts.py server/services/fb_account_service.py tests/test_fb_accounts_router.py
git commit -m "feat(f7): add POST /fb-accounts/{id}/re-upload-cookie endpoint"
```

---

#### F7-4: API client wrapper (frontend)

**Objective:** Tambah `api.reValidateFBAccount(id)` dan `api.reUploadFBCookie(id, raw)` di `dashboard/src/services/api.ts`.

**Files:**
- Modify: `dashboard/src/services/api.ts`

**Step 1: Append methods**

```ts
async reValidateFBAccount(id: number) {
  return this.request(`/fb-accounts/${id}/re-validate`, { method: 'POST' })
}

async reUploadFBCookie(id: number, raw: string) {
  return this.request(`/fb-accounts/${id}/re-upload-cookie`, {
    method: 'POST',
    body: JSON.stringify({ raw_cookies: raw }),
  })
}
```

**Step 2: Commit**

```bash
git add dashboard/src/services/api.ts
git commit -m "feat(f7): api client for re-validate + re-upload cookie"
```

---

#### F7-5: UI — Re-validate button di FBAccounts card

**Objective:** Tambah tombol `Re-validate` di card akun (semua status, bukan cuma EXPIRED — biar user bisa peace-of-mind check kapan aja). Toast hasil success/fail. Invalidate `fbAccountCurrent`.

**Files:**
- Modify: `dashboard/src/pages/FBAccounts.tsx` (button row ~346-372, tambah mutation ~178)

**Step 1: Add mutation**

```tsx
const revalidateMutation = useMutation({
  mutationFn: (id: number) => api.reValidateFBAccount(id),
  onSuccess: (data: { valid: boolean }) => {
    queryClient.invalidateQueries({ queryKey: ['fbAccountCurrent'] })
    if (data.valid) {
      toast.success('Cookie masih valid, akun balik ACTIVE')
    } else {
      toast.error('Cookie gak valid lagi — re-upload cookie baru')
    }
  },
  onError: (err: any) => toast.error(err.message || 'Gagal re-validate'),
})
```

**Step 2: Render button (next to Edit)**

```tsx
{isCookieAccount && (
  <Button
    size="sm"
    variant="outline"
    onClick={() => revalidateMutation.mutate(account.id)}
    disabled={revalidateMutation.isPending}
  >
    <ShieldCheck />
    Re-validate
  </Button>
)}
```

**Step 3: Commit**

```bash
git add dashboard/src/pages/FBAccounts.tsx
git commit -m "feat(f7): add Re-validate button on FBAccount card"
```

---

#### F7-6: UI — Re-upload Cookie dialog

**Objective:** Dialog terpisah yang reuse `CookieInstructions` + textarea + preview + confirm, tapi panggil endpoint re-upload (bukan connect). Dibuka dari tombol `Re-upload Cookie` yang cuma muncul kalau status EXPIRED/CHECKPOINT atau via explicit action menu.

**Files:**
- Modify: `dashboard/src/pages/FBAccounts.tsx`

**Step 1: State tambahan**

```tsx
const [reuploadOpen, setReuploadOpen] = useState(false)
const [reuploadRaw, setReuploadRaw] = useState('')
const [reuploadPreview, setReuploadPreview] = useState<CookiePreview | null>(null)

const reuploadPreviewMutation = useMutation({
  mutationFn: (raw: string) => api.previewFBCookie(raw),
  onSuccess: (data) => { setReuploadPreview(data.preview); toast.success('Cookie valid') },
  onError: (err: any) => { setReuploadPreview(null); toast.error(err.message) },
})

const reuploadMutation = useMutation({
  mutationFn: ({ id, raw }: { id: number; raw: string }) => api.reUploadFBCookie(id, raw),
  onSuccess: () => {
    queryClient.invalidateQueries({ queryKey: ['fbAccountCurrent'] })
    toast.success('Cookie di-replace, akun balik ACTIVE')
    setReuploadOpen(false)
    setReuploadRaw('')
    setReuploadPreview(null)
  },
  onError: (err: any) => toast.error(err.message || 'Gagal re-upload'),
})
```

**Step 2: Button (conditional)**

```tsx
{isCookieAccount && (account.status === 'EXPIRED' || account.status === 'CHECKPOINT') && (
  <Button size="sm" onClick={() => setReuploadOpen(true)}>
    <Cookie />
    Re-upload Cookie
  </Button>
)}
```

**Step 3: Dialog (copy setup cookie tab, swap connect → reupload).**

**Step 4: Commit**

```bash
git add dashboard/src/pages/FBAccounts.tsx
git commit -m "feat(f7): add Re-upload Cookie dialog for in-place cookie refresh"
```

---

#### F7-7: AccountStatusBanner — deep-link ke re-upload action

**Objective:** Pass `?action=reupload` query param saat admin klik `Buka Accounts` dari banner EXPIRED/CHECKPOINT; FBAccounts page auto-open `reuploadOpen=true`.

**Files:**
- Modify: `dashboard/src/components/account-status-banner.tsx`
- Modify: `dashboard/src/pages/FBAccounts.tsx` (read `searchParams` effect)

**Step 1**: banner `onClick={() => navigate('/accounts?action=reupload')}`.
**Step 2**: FBAccounts page `useEffect` — kalau `searchParams.get('action') === 'reupload' && account`, panggil `setReuploadOpen(true)` sekali lalu `setSearchParams({})`.

**Step 3: Commit**

```bash
git add dashboard/src/components/account-status-banner.tsx dashboard/src/pages/FBAccounts.tsx
git commit -m "feat(f7): deep-link banner Buka Accounts → re-upload dialog"
```

---

#### F7-8: Verify + Deploy

- Focused test: `pytest tests/test_fb_accounts_router.py -v` (expect all pass termasuk new re-validate + re-upload).
- Full suite: `pytest` (expect 566 + 8 new = 574 pass, ±).
- Lint: `ruff check server tests`.
- Frontend build: `cd dashboard && npm run build` (expect green).
- `git diff --check`.
- Push branch → `git pull origin main` di rdpkhorur → restart systemd `fb-bot-api` → smoke:
  - `curl /api/v1/fb-accounts/1/re-validate` dengan admin token → 200.
  - UI `/accounts` → klik Re-validate → toast OK, banner clear kalau status aktif.
  - Simulate expired (paste cookie asal) → 400 + banner merah → re-upload flow → banner hilang.
- Update activity log di `development-behavior.md` (lihat §4).

---

## 3. Phase H Candidates — Pilih Setelah F7 Kelar

Urutan berdasarkan value:user-effort. Pilih satu untuk sprint berikutnya, sisanya masuk parking lot.

### 3.1 H-A: Notification Channel (Telegram/webhook) — **RECOMMENDED**

**Value:** Saat ini admin harus aktif buka UI buat tau akun expired atau trending post baru. Bot autonomous butuh push-notification.

**Scope:**
- Setting `notification_webhook_url` + `notification_telegram_chat_id` di admin settings.
- Notifier service dispatch saat: `FBAccount.status → EXPIRED/CHECKPOINT/BLOCKED`, `ScannerRun.status → FAILED`, trending post baru dengan score > threshold, quota rate-limit penuh.
- Rate-limit notif sendiri (max 1/jam per event type) biar gak spam.

### 3.2 H-B: Auto-send Mode

**Value:** Saat ini commenting 100% manual. Kalau user trust template/AI draft, auto-send bisa scale throughput.

**Scope:**
- `FBAccount.auto_send_enabled` toggle + confidence threshold (score post > X, draft length in range, no forbidden phrase).
- Celery task `auto_send_tick` jalan tiap 10 menit: pick eligible post (NEW, score > threshold, quota allow), generate AI draft, send.
- Safety: max-send-per-tick = 1, respect 5/6h quota, stop kalau 2x berturut-turut FAILED.
- UI: dashboard panel "Auto-send" + history filter "sent by:auto|manual".

**Risk:** lebih gede blow-up kalau FB deteksi pattern. Butuh hardened jitter + session rotation.

### 3.3 H-C: Analytics Dashboard

**Value:** Ops insight — komen per-hari, success-rate per-source, trend score distribution, error taxonomy.

**Scope:**
- Endpoint `GET /api/v1/analytics/summary?from=&to=`.
- Page `/analytics` dengan chart (recharts / tremor).
- Metrics: total sent, success rate, avg score, top sources by engagement, CommentHistory error breakdown.

### 3.4 H-D: Multi-account Rotation (BIG)

**Value:** Extend bot beyond single-account limit, distribute risk across 3-5 akun.

**Scope besar:** break single-account invariant di schema + router + UI. Separate scan account vs post account. Round-robin dengan per-account quota. Butuh planning terpisah — **jangan ambil tanpa discovery meeting dulu**.

### 3.5 H-E: Per-Category Template

**Value:** Sekarang ada 1 template aktif global. Komen generic buat semua post terasa robotik.

**Scope:**
- Multiple active template, tagged per category (jualan, curhat, lowongan-kerja, info, dll).
- Keyword filter + scorer sudah tag category di post — wire ke template picker.
- UI: template list (CRUD), label category multi-select.

### 3.6 H-F: Session Rotation & Browser Fingerprint Hardening

**Value:** Anti-detection. Sekarang Playwright pake default fingerprint setiap run.

**Scope:** persist browser context per-akun, rotate user-agent + viewport (reasonable pool), persist localStorage. Incremental — tidak perlu dalam 1 sprint besar.

---

## 4. Parking Lot (Ide, belum di-plan)

- **Draft history per post** — simpan semua draft (AI + manual + template), user bisa revert.
- **Comment reply watcher** — setelah send, cek apakah ada reply ke komen kita, notify admin.
- **A/B test template** — 2 template aktif, alternate pick, track which converts better (engagement di reply thread).
- **Bulk skip by regex** — admin mark skip buat pattern post (gambar tertentu, author blacklist).
- **Dark-mode toggle user-level preference** (saat ini dark = default system, tapi belum ada toggle explicit di header).

---

## 5. Activity Log Rollup (ringkas buat re-insert ke development-behavior.md)

Setelah F7 selesai, trim log di `.kiro/steering/development-behavior.md` ke 8 entry terbaru dengan urutan (new → old):

1. `2026-MM-DD F7 account recovery` — (diisi pas F7 done, plus HEAD sha).
2. `2026-05-12 misc polish` — `029144e` belum komen rename + Fresh badge + UTC ISO tz fix (`e2ba6c8`) + scan-now UI (`c3e1213`) + scanner audit table (`ddb35d2`).
3. `2026-05-12 account banner + auth probe` — `2f31152` AccountStatusBanner + `34cc430` scanner probe + `00f6b9e` sender probe + `f2d69a9` fb_auth_probe module.
4. `2026-05-11 ai draft integration` — `e33a1ac`..`d7e2ca7` AIDraftService + `/trending/{id}/ai-draft` + sparkle wand UI.
5. `2026-05-11 phase G hardening` — `e653106` ErrorBoundary + `a3e8459` docs rewrite + `9a7d808` code-split + global quota + dark default + `f94333d` cleanup review queue.
6. `2026-05-10 f6 history ui` — `d82ed4d` GET /history + `316cf17` History page.
7. `2026-05-10 photo viewer composer + multi-locale` — `ac488ea` + `74947b0` + `c107c1a` + chore probes.
8. `2026-05-10 f5 send endpoint + ui wiring` — `45eda50` (existing baseline).

Entry `2026-05-10 F4a rate limit service` dan yang lebih lama boleh didrop sesuai rule "keep hanya 8 entry terbaru".

---

## 6. How to Resume

Kalau lanjut di sesi depan:

1. Baca file ini dulu (`.kiro/steering/post-f5-roadmap.md`).
2. Pilih task dari §2 yang `[ ]`, atau §3 kalau F7 udah kelar.
3. RED/GREEN TDD: test dulu, implementation kedua, commit ketiga.
4. Update status task di file ini dengan legend `[x]` setelah commit + deploy OK.
5. Rollup activity log ke `development-behavior.md` setelah phase kelar, keep top 8.

**Current Next Step:** F7 shipped (HEAD `d976df3`). Pilih salah satu Phase H candidate dari §3 buat sprint berikutnya. Gue rekomendasi H-A (Notification channel).
