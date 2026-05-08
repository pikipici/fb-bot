# Fase 1 — Backend Auth + RBAC + Minimal Dashboard

> Status: IN PROGRESS
> Started: 2026-05-09

---

## Objective

Implementasi authentication, role-based access control, dan minimal dashboard untuk approve/reject draft.

---

## Tasks

### 1. Auth Router (login, refresh, register)
- [ ] Buat `server/routers/auth.py` — endpoint `/api/v1/auth/login`, `/api/v1/auth/refresh`, `/api/v1/auth/register`
- [ ] Login: validasi username + password → return access + refresh token
- [ ] Refresh: validasi refresh token → return new access token
- [ ] Register: hanya admin yang bisa create user baru (kecuali first user = auto admin)
- [ ] Include router di `server/main.py`

### 2. User Service & DB Wiring
- [ ] Buat `server/services/user_service.py` — create_user, get_user_by_username, verify_credentials
- [ ] Seed first admin user (via CLI command atau auto-seed)
- [ ] Alembic init + first migration untuk tabel users

### 3. RBAC Middleware
- [ ] Verify `require_role()` dependency berfungsi di semua protected routes
- [ ] Test: viewer gak bisa approve, operator bisa approve, admin bisa semua

### 4. Minimal React Dashboard
- [ ] Init React + TypeScript + Vite project di `dashboard/`
- [ ] Install Tailwind CSS + shadcn/ui
- [ ] Buat halaman login
- [ ] Buat halaman review queue (list pending drafts + approve/reject button)
- [ ] Auth state management (Zustand) — simpan token, auto-refresh
- [ ] API service layer (TanStack Query)

### 5. Testing
- [ ] `tests/test_auth.py` — test login, refresh, register, role check
- [ ] `tests/test_user_service.py` — test create user, verify credentials
- [ ] Run tests di rdpkhorur via SSH

### 6. Verification & Deploy
- [ ] Semua test pass di rdpkhorur
- [ ] Push ke GitHub
- [ ] Pull di rdpkhorur
- [ ] Server bisa start dan health check OK
- [ ] Login flow works end-to-end

---

## Notes

- Auth skeleton udah ada dari fase 0, tinggal lengkapin wiring ke DB
- Dashboard minimal dulu — fokus ke auth flow + approve/reject, styling nanti
- First user auto-jadi admin kalau belum ada user di DB
