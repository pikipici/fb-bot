# Fase 1 — Backend Auth + RBAC + Minimal Dashboard

> Status: IN PROGRESS
> Started: 2026-05-09

---

## Objective

Implementasi authentication, role-based access control, dan minimal dashboard untuk approve/reject draft.

---

## Tasks

### 1. Auth Router (login, refresh, register)
- [x] Buat `server/routers/auth.py` — endpoint `/api/v1/auth/login`, `/api/v1/auth/refresh`, `/api/v1/auth/register`
- [x] Login: validasi username + password → return access + refresh token
- [x] Refresh: validasi refresh token → return new access token
- [x] Register: hanya admin yang bisa create user baru (kecuali first user = auto admin)
- [x] Include router di `server/main.py`

### 2. User Service & DB Wiring
- [x] Buat `server/services/user_service.py` — create_user, get_user_by_username, verify_credentials
- [x] Seed first admin user (via CLI command atau auto-seed)
- [x] Alembic init + first migration untuk tabel users

### 3. RBAC Middleware
- [x] Verify `require_role()` dependency berfungsi di semua protected routes
- [x] Test: viewer gak bisa approve, operator bisa approve, admin bisa semua

### 4. Minimal React Dashboard
- [x] Init React + TypeScript + Vite project di `dashboard/`
- [x] Install Tailwind CSS + Zustand + TanStack Query + React Router
- [x] Buat halaman login
- [x] Buat halaman review queue (list pending drafts + approve/reject button)
- [x] Auth state management (Zustand) — simpan token, auto-refresh
- [x] API service layer (TanStack Query)

### 6. Verification & Deploy
- [x] Semua test pass di rdpkhorur (24 passed)
- [x] Push ke GitHub
- [ ] Pull di rdpkhorur + build dashboard
- [ ] Server bisa start dan health check OK
- [ ] Login flow works end-to-end

---

## Notes

- Auth skeleton udah ada dari fase 0, tinggal lengkapin wiring ke DB
- Dashboard minimal dulu — fokus ke auth flow + approve/reject, styling nanti
- First user auto-jadi admin kalau belum ada user di DB
