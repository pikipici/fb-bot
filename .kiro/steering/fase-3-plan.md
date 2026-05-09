# Fase 3 — Draft Engine tanpa AI

> Status: IN PROGRESS
> Started: 2026-05-09

---

## Objective

Implementasi draft engine yang fully functional tanpa AI layer — template statis + semi-dinamis dengan fallback chain dan validator keamanan.

---

## Tasks

### 1. Draft Engine Enhancement
- [ ] Lengkapi fallback chain: semi-dynamic → static → NEEDS_MANUAL_WRITE
- [ ] Validator: panjang, forbidden phrases, no links, fingerprint dedup
- [ ] Support multiple templates per category/language
- [ ] Randomize template selection untuk variasi

### 2. Draft Service (DB Integration)
- [ ] Buat `bot/services/draft_service.py` — save draft, update status, get pending
- [ ] Wire draft engine output ke DB
- [ ] Link draft ke post (post_id foreign key)

### 3. Full Flow Integration
- [ ] Connect pipeline → draft engine → DB
- [ ] Buat `bot/modules/orchestrator.py` — run full cycle: collect → filter → score → draft
- [ ] Queued posts otomatis masuk draft generation

### 4. API Wiring (Backend)
- [ ] Lengkapi `server/routers/drafts.py` — query real data dari DB
- [ ] Lengkapi `server/routers/approvals.py` — update draft status + audit log
- [ ] Lengkapi `server/routers/posts.py` — query real posts

### 5. Testing
- [ ] `tests/test_draft_engine.py` — fallback chain, validator, fingerprint
- [ ] `tests/test_draft_service.py` — DB operations
- [ ] Run all tests di rdpkhorur

### 6. Verification
- [ ] All tests pass
- [ ] Push ke GitHub
- [ ] Pull di rdpkhorur, verify clean

---

## Notes

- Draft engine skeleton udah ada, tinggal enhance validator + randomization
- Orchestrator jadi entry point buat full cycle
- API wiring bikin dashboard bisa consume real data
