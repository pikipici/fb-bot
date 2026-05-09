# Fase 2 — Scoring Engine & Config

> Status: IN PROGRESS
> Started: 2026-05-09

---

## Objective

Implementasi scoring engine yang fully functional — menghitung skor prioritas post berdasarkan engagement, freshness, relevance, dan risk. Integrasi dengan config JSON dan wiring ke database.

---

## Tasks

### 1. Scoring Engine Enhancement
- [ ] Lengkapi `bot/modules/scorer.py` dengan keyword matching logic
- [ ] Integrasi dengan `keywords.json` dan `blacklist.json` untuk relevance + risk detection
- [ ] Add language filter support
- [ ] Add duplicate detection (by fb_post_id)

### 2. Detector Module
- [ ] Buat `bot/modules/detector.py` — keyword matching, risk tagging, language detection
- [ ] Filter chain: age → engagement → duplicate → keyword → risk

### 3. Post Processing Pipeline
- [ ] Buat `bot/modules/pipeline.py` — orchestrate: filter → score → queue decision
- [ ] Integrasi scorer + detector dalam satu flow
- [ ] Output: post dengan score + status (QUEUED / FILTERED_OUT)

### 4. Database Integration
- [ ] Buat `bot/services/post_service.py` — save posts, update score/status, check duplicates
- [ ] Wire pipeline output ke DB via post_service

### 5. Testing
- [ ] `tests/test_scorer.py` — unit test scoring formula, edge cases
- [ ] `tests/test_detector.py` — keyword match, risk tag, language filter
- [ ] `tests/test_pipeline.py` — end-to-end filter + score flow
- [ ] Run tests di rdpkhorur

### 6. Verification
- [ ] All tests pass
- [ ] Push ke GitHub
- [ ] Pull di rdpkhorur, verify import clean

---

## Notes

- Scorer skeleton udah ada dari fase 0, tinggal enhance + integrate
- Detector adalah module baru yang handle filtering logic
- Pipeline orchestrates detector + scorer dalam satu pass
