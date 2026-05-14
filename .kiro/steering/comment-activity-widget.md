# Comment Activity Widget — Plan

Living plan: ganti semantik Quota (preflight gate) jadi pure Activity Counter
("Komen hari ini: X"). Limit masih ada di backend tapi di-bypass via env
(`MAX_COMMENTS_PER_WINDOW=9999`) — rollback-friendly kalau observation Phase I
nunjuk komen pattern problematic.

## Context & Rationale

- Phase F4a rate-limit (5 komen/6 jam, rolling) dibikin **sebelum** ada data
  real. Asumsi anti-FB-block, bukan benchmark.
- User decision: **pertahanin service, bypass limit, ubah UX jadi informatif
  (bukan gate)**. Tampilan "berapa komen hari ini" aja, tanpa blocking.
- Observation window Phase I Session Hardening lagi OPEN (Tstart 2026-05-13
  11:05 UTC, target recheck 24h + 48h). Rip-out RateLimitService =
  confounding variable kalau akun flip EXPIRED/CHECKPOINT. Solusi: **env
  override** biar kode utuh, rollback cepat.

## Non-Goals

- **NOT** removing `RateLimitService` class.
- **NOT** removing `GET /api/v1/rate-limit/status` endpoint (tetep hidup, FE
  tinggal unconsume).
- **NOT** removing `record_send` / `CommentHistory` write path.
- **NOT** ngubah preflight `check_allowed` di router send (praktis pass-through
  dgn limit 9999).

## Tasks

### Backend

- [ ] **B-1 Env override limit**: confirm `MAX_COMMENTS_PER_WINDOW` bisa di-override
      via env var. Kalau belum, patch `RateLimitService` baca `os.getenv(
      "MAX_COMMENTS_PER_WINDOW", 5)`. Test: unit test env-respected.
- [ ] **B-2 CommentActivityService**: new module `bot/services/comment_activity.py`.
      Method `today_count(db, tz='Asia/Jakarta') -> int` count
      `CommentHistory.status='SENT'` dengan `created_at` dalam window
      `[today_00:00 WIB, tomorrow_00:00 WIB)`. Boundary test WIB (00:00 WIB
      = 17:00 UTC hari sebelumnya). Timezone pake `zoneinfo.ZoneInfo`.
- [ ] **B-3 Router `/api/v1/comment-activity/today`**: GET, any-auth (match
      pola `/rate-limit/status`). Response `{count_today: int, date:
      "YYYY-MM-DD", tz: "Asia/Jakarta"}`. Test: happy, empty=0, auth.
- [ ] **B-4 Server env `.env` rdpkhorur**: set `MAX_COMMENTS_PER_WINDOW=9999`,
      restart `fb-bot-api fb-bot-worker fb-bot-beat`. Smoke: curl
      `/rate-limit/status` → `remaining` ~9999.

### Frontend

- [ ] **F-1 API client**: `api.getCommentActivity()` di `src/services/api.ts`
      → `GET /api/v1/comment-activity/today`.
- [ ] **F-2 Widget component**: baru `src/components/CommentActivityWidget.tsx`
      render teks kecil `"Komen hari ini: {count}"`. `useQuery(['comment-
      activity-today'])` polling 30s.
- [ ] **F-3 Header swap**: di layout header global, ganti mount `QuotaWidget`
      jadi `CommentActivityWidget`. Hapus `QuotaWidget.tsx` file (sudah ga
      kepake di mana-mana lagi).
- [ ] **F-4 Trending page cleanup**: hapus Quota Card banner di atas filter
      row + impor `Gauge` icon kalau ga dipake lagi + query
      `['rate-limit-status']` di page ini. Send button disabled logic
      disederhanain: `!isAdmin || !draftTrimmed`.
- [ ] **F-5 Mutation invalidate**: `sendComment` onSuccess invalidate
      `['trending']` + `['comment-activity-today']` (ganti yang lama).
- [ ] **F-6 Build + lint**: `npm run lint` + `npm run build` clean di
      rdpkhorur.

### Verify & Deploy

- [ ] **V-1 Focused test**: service + router test pass lokal (jalan di server).
- [ ] **V-2 Full suite**: `pytest` di rdpkhorur, baseline 677 → expected +3-6
      new test.
- [ ] **V-3 Production build FE**: bundle success.
- [ ] **V-4 Commit**: relay branch `agent-relay-<hash>`, prefix `feat:` +
      `refactor:` split kalau perlu.
- [ ] **V-5 Deploy**: push → server `git pull` → `systemctl restart`, SHA
      match semua 3 host.
- [ ] **V-6 Smoke live**:
  - `curl /api/v1/rate-limit/status` → `limit: 9999, allowed: true`
  - `curl /api/v1/comment-activity/today` → `{count_today, date, tz}`
  - Dashboard header nampilin "Komen hari ini: N"
  - Trending page: ga ada Quota Card banner, Send button ga blocked quota.

## Rollback Plan

Kalau observation Phase I fail & ternyata komen-pattern dicurigai confound:
1. Set `MAX_COMMENTS_PER_WINDOW` back ke angka konservatif (misal 5 atau 20) di
   server `.env`, restart services. Preflight langsung aktif lagi tanpa code
   change.
2. Kalau mau UI rate-limit gate balik, revert FE commit F-3/F-4 (`git revert`).

## Timezone Edge Cases

- WIB (Asia/Jakarta) = UTC+7, **no DST**, jadi offset stabil.
- Calendar day boundary WIB:
  - 00:00 WIB = 17:00 UTC hari kalender sebelumnya.
  - 2026-05-13 21:12 WIB = 2026-05-13 14:12 UTC (contoh ongoing session).
  - Next reset 2026-05-14 00:00 WIB = 2026-05-13 17:00 UTC.
- Implement via `datetime.now(ZoneInfo("Asia/Jakarta"))` → normalize ke
  `date()` → range `[midnight_wib, midnight_wib+1day)` → convert kembali ke
  UTC buat query `CommentHistory.created_at`.

## Status

**Not started.** Menunggu approval `gas` dari user.
