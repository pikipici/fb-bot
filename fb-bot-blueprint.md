# 🤖 FB Engagement Assistant — Blueprint Sistem (Revisi Lengkap)

> **Tujuan:** Sistem ini memantau postingan publik yang relevan di Facebook, memberi skor prioritas, lalu menyusun **draft respons** yang akan ditinjau dan disetujui operator manusia sebelum ada aksi apa pun ke Facebook.
>
> Fokus utama: **monitoring, scoring, dan drafting dengan human-in-the-loop**, bukan auto-posting promosi.

---

## 📌 Gambaran Umum

Sistem dibangun sebagai beberapa lapisan yang saling terpisah tapi terintegrasi:

1. **Collector Core** – mengambil kandidat postingan dari target publik (API resmi bila memungkinkan, Playwright scraping sebagai fallback).
2. **Scoring Engine** – menghitung skor prioritas berdasarkan engagement, kebaruan, relevansi, dan risiko.
3. **Draft Response Engine** – membangun draft komentar dengan rantai fallback (AI opsional → semi-dinamis → statis → manual write).
4. **Backend API** – FastAPI + WebSocket untuk workflow review, approval, audit log, dan analitik.
5. **Frontend Dashboard** – React + TypeScript untuk monitoring, review, approval, dan pengelolaan konfigurasi.
6. **Monitoring & Notifikasi** – Sentry, logging terstruktur, dan notifikasi Telegram (alert + report periodik).

Seluruh tindakan keluar (komentar / balasan) wajib melewati approval operator. Tidak ada aksi auto-post ke Facebook tanpa persetujuan manusia.[file:1]

---

## 📐 Arsitektur Sistem

```text
[Celery Beat]
     ↓
[Target Scheduler]
     ↓
[Celery Worker: Collector] → [Parser + Normalizer]
     ↓
[Relevance + Risk Filter] → [Scoring Engine]
     ↓
[Draft Response Engine]
     ↓
[Review Queue] ←→ [Redis Cache]
     ↓
[SQLite/PostgreSQL DB] → [Logger + Metrics + Recovery Manager]
     ↓
[FastAPI Backend + Auth + Rate Limit]
     ↓              ↕ Redis pub/sub
[React Dashboard + WebSocket Auth + Analytics]
     ↓
[Telegram Alert / Daily & Weekly Report]
     ↑
[Backup Service] ←─── [Database]
     ↓
[Sentry — Error Monitoring semua lapisan]
```

Perbedaan utama dari desain bot promosi auto-comment klasik adalah pemisahan eksplisit antara **deteksi**, **penyusunan draft**, dan **approval manual**. Pola ini menurunkan risiko spam, salah konteks, dan pelanggaran kebijakan platform, sekaligus membuat sistem lebih mudah diaudit dan dihentikan kapan saja.[file:1]

---

## 🔧 Tech Stack

### Core Service (Python)

| Komponen               | Library / Tools           | Fungsi utama                                                                       |
|------------------------|---------------------------|------------------------------------------------------------------------------------|
| Browser automation     | Playwright                | Mengambil data dari tampilan web publik (mode `scrape_public`)                    |
| HTTP request           | httpx                     | Fetch metadata, health check, dan integrasi API resmi (mode `api_first`)          |
| HTML parsing           | BeautifulSoup4            | Parsing fallback untuk struktur HTML yang kompleks                                 |
| Scheduler / worker     | Celery + Celery Beat      | Task queue terdistribusi, retry, dan penjadwalan koleksi data                      |
| Message broker & cache | Redis                     | Broker Celery, review queue caching, pub/sub WebSocket                             |
| Database awal          | SQLite (WAL mode)         | Fase single-node / prototipe                                                       |
| Database produksi      | PostgreSQL                | Fase multi-worker dan beban tulis tinggi                                           |
| ORM / migrasi          | SQLAlchemy + Alembic      | Abstraksi DB + migrasi skema versioned                                             |
| Konfigurasi            | python-dotenv + pydantic-settings | Memuat dan memvalidasi konfigurasi environment                                |
| Retry terarah          | tenacity                  | Retry untuk error jaringan / sementara                                             |
| Notifikasi             | python-telegram-bot       | Alert insiden + ringkasan harian / mingguan                                       |
| Logging                | logging / structlog       | Log terstruktur, rotasi file, korelasi request                                    |
| Error monitoring       | sentry-sdk                | Error & performance tracing di worker dan backend                                  |
| Rate limiting API      | slowapi                   | Proteksi endpoint sensitif dari brute force & abuse                               |
| Analitik internal      | pandas                    | Perhitungan metrik dan laporan periodik                                           |

### Backend Dashboard

| Komponen        | Library / Tools                |
|-----------------|--------------------------------|
| API server      | FastAPI                        |
| WebSocket       | FastAPI WebSocket + Redis pub/sub |
| Auth            | JWT access + refresh token     |
| Role & izin     | RBAC: `viewer`, `operator`, `admin` |
| DB integration  | SQLAlchemy ORM                 |
| Server          | Uvicorn / Gunicorn             |
| Reverse proxy   | Nginx / Caddy                  |
| Error monitoring| sentry-sdk[fastapi]            |

### Frontend Dashboard

| Komponen        | Library / Tools |
|-----------------|-----------------|
| Framework       | React + TypeScript |
| Styling         | Tailwind CSS    |
| UI components   | shadcn/ui       |
| State management| Zustand         |
| Data fetching   | TanStack Query  |
| Forms           | React Hook Form |
| Charts          | Recharts        |
| Error monitoring| @sentry/react   |

### Dependensi Python (Ringkasan)

```txt
playwright
beautifulsoup4
httpx
python-dotenv
pydantic-settings
celery[redis]
celery[beat]
redis
fastapi
uvicorn
sqlalchemy
alembic
slowapi
cryptography
tenacity
pandas
python-telegram-bot
sentry-sdk[fastapi]
```

**Catatan revisi:**

- **APScheduler diganti Celery + Celery Beat + Redis** untuk mendukung multi-worker, retry bawaan, dan observabilitas task yang lebih baik.[file:1]
- **Redis** berperan ganda sebagai broker Celery, cache ringan, dan layer pub/sub untuk WebSocket.[file:1]
- **Sentry** diintegrasikan di tiga titik: FastAPI backend, Celery worker, dan React frontend.[file:1]
- Lapisan **AI draft** bersifat opsional; integrasi ke OpenAI/Ollama/LLM lain diatur lewat feature flag.[file:1]

---

## 📁 Struktur Folder Project

```text
fb-engagement-assistant/
├── bot/
│   ├── main.py
│   ├── celery_app.py
│   ├── config/
│   │   ├── targets.json
│   │   ├── response_templates.json
│   │   ├── keywords.json
│   │   ├── blacklist.json
│   │   ├── scoring_rules.json
│   │   ├── feature_flags.json
│   │   └── rate_limits.json
│   ├── modules/
│   │   ├── collector.py
│   │   ├── parser.py
│   │   ├── detector.py
│   │   ├── scorer.py
│   │   ├── draft_engine.py
│   │   ├── approval_queue.py
│   │   ├── recovery.py
│   │   ├── notifier.py
│   │   ├── backup.py
│   │   ├── config_watcher.py
│   │   ├── rate_guard.py
│   │   ├── circuit_breaker.py
│   │   └── logger.py
│   ├── data/
│   │   ├── exports/
│   │   └── app.db
│   └── logs/
│       ├── activity.log
│       ├── error.log
│       └── heartbeat.log
│
├── server/
│   ├── main.py
│   ├── auth.py
│   ├── websocket.py
│   ├── database.py
│   ├── models.py
│   └── routers/
│       ├── posts.py
│       ├── drafts.py
│       ├── approvals.py
│       ├── stats.py
│       ├── settings.py
│       ├── reports.py
│       └── health.py
│
├── dashboard/
│   └── src/
│       ├── App.tsx
│       ├── components/
│       │   ├── Navbar.tsx
│       │   ├── StatsCards.tsx
│       │   ├── ReviewQueue.tsx
│       │   ├── DraftDetail.tsx
│       │   ├── ActivityLog.tsx
│       │   ├── HeatmapChart.tsx
│       │   └── SettingsPanel.tsx
│       ├── hooks/
│       │   └── useWebSocket.ts
│       ├── store/
│       │   └── appStore.ts
│       └── types/
│           └── index.ts
│
├── deploy/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── nginx.conf
│   └── systemd/
│       ├── collector.service
│       └── dashboard.service
│
├── tests/
│   ├── test_scorer.py
│   ├── test_draft_engine.py
│   ├── test_recovery.py
│   ├── test_circuit_breaker.py
│   └── test_auth.py
│
└── docs/
    ├── api.md
    ├── recovery-runbook.md
    ├── security.md
    └── deployment.md
```

Struktur ini memperjelas pemisahan antara bot, backend API, frontend dashboard, deployment, testing, dan dokumentasi.[file:1]

---

## 🔄 Alur Kerja Sistem

### Fase 1 — Inisialisasi

```text
main.py dijalankan
    ↓ Baca environment dan validasi konfigurasi
    ↓ Muat config JSON (targets, templates, keywords, blacklist, scoring rules, feature flags, rate_limits)
    ↓ Inisialisasi database (SQLite / PostgreSQL)
    ↓ Jika SQLite: aktifkan WAL mode dan busy_timeout
    ↓ Jalankan Celery worker + Celery Beat + heartbeat + backup scheduler
    ↓ Jalankan FastAPI backend dengan auth & rate limiter
    ↓ Inisialisasi Sentry SDK di worker dan backend
    ↓ Sistem siap menerima jadwal dan request dashboard
```

### Fase 2 — Pengambilan Data (Collector)

Collector mendukung dua mode utama, dikontrol lewat `targets.json`:

1. **`mode: "api_first"`** – gunakan endpoint resmi (Graph API) bila tersedia dan token valid.
2. **`mode: "scrape_public"`** – gunakan Playwright untuk mengekstrak data dari halaman publik.

```text
Celery Beat memicu task collector
    ↓ Scheduler memilih target berdasarkan prioritas, cooldown, dan health_status
    ↓ Untuk setiap target aktif:
         - Panggil rate_guard.check_and_reserve(target_id)
              - Jika bucket global/per-target penuh → target dilewati untuk run ini
         - mode api_first: panggil API resmi
         - mode scrape_public: jalankan Playwright headless, scroll hingga batas
    ↓ Ekstrak metadata: fb_post_id, url, author, timestamp, engagement, cuplikan teks, bahasa
    ↓ Normalisasi ke struktur Post internal
    ↓ Simpan ke tabel posts (status awal: "QUEUED" atau "FILTERED_OUT")
    ↓ Jika parsing gagal berulang: health_score turun, status target bisa menjadi DEGRADED/SUSPENDED
```

Untuk mengurangi risiko deteksi dan blokir:
- Batasi jumlah request per target per run dan per menit/jam secara global.
- Tambahkan jeda acak antar scroll / request.
- Gunakan User-Agent yang wajar dan konsisten.
- Jika mendeteksi CAPTCHA, layout tidak dikenali, atau sinyal rate limit, tandai target DEGRADED/SUSPENDED dan terapkan backoff di `rate_guard`.

### Fase 3 — Deteksi & Skoring

Sebelum skor dihitung, post melewati beberapa filter:

- Umur post (maksimal `max_post_age_hours`).
- Minimal engagement (`min_engagement`).
- Duplikasi (berdasarkan `fb_post_id`).
- Keyword whitelist & blacklist.
- Tag risiko (politik, SARA, dsb.).

Skor dihitung dengan formula generik yang dikonfigurasi lewat `scoring_rules.json`:

```text
score = (w_engagement * normalized_engagement)
      + (w_freshness * freshness_score)
      + (w_relevance * relevance_score)
      + (w_risk * risk_penalty)
```

- `normalized_engagement` memakai skala/log dari like + comment + share.
- `freshness_score` berada di rentang 0–1 (post sangat baru → skor mendekati 1).
- `relevance_score` 0–1 berdasarkan kecocokan keyword / embedding.
- `risk_penalty` bernilai 0 untuk post aman, negatif untuk topik sensitif.

Post dengan skor ≥ `queue_score_min` akan dimasukkan ke **review queue** (Redis) dan status di DB diperbarui.[file:1]

### Fase 4 — Draft Respons

```text
Worker mengambil post dari review queue
    ↓ Draft Engine menentukan jalur:
         - AI draft (jika feature flag aktif)
         - Template semi-dinamis (berdasarkan kategori/keyword)
         - Template statis
    ↓ Jika AI dipilih:
         - Bangun prompt berdasarkan ai_prompts.json + konteks post
         - Kirim ke provider LLM
    ↓ Jalankan validasi keamanan draft:
         - panjang teks dalam batas aman
         - tidak mengandung kata agresif / klaim berlebihan
         - tidak menyertakan link langsung (kecuali operator yang menambah)
         - fingerprint unik, tidak terlalu mirip dengan draft lain
         - tidak mengandung frasa terlarang brand
    ↓ Jika valid → simpan sebagai draft status PENDING_REVIEW
    ↓ Jika gagal → fallback ke jalur berikutnya (semi-dinamis → statis)
    ↓ Jika semua jalur gagal → status NEEDS_MANUAL_WRITE
```

### Fase 5 — Review & Approval

```text
Operator membuka dashboard
    ↓ Lihat daftar draft PENDING_REVIEW beserta konteks post
    ↓ Lakukan salah satu aksi:
         - Approve (tanpa edit)
         - Edit lalu approve
         - Reject (dengan alasan)
    ↓ Simpan aksi di tabel approvals + audit_logs
    ↓ Update statistik template dan kualitas draft (approval/edit/reject rate)
```

Implementasi aktual posting ke Facebook (jika dipakai) akan berada di modul terpisah dan selalu berada di belakang feature flag berisiko tinggi.

---

## 🧠 Draft Engine

Draft Engine terdiri dari tiga lapisan:

1. **Template statis** – kalimat aman untuk kasus-kasus umum.
2. **Template semi-dinamis** – menyesuaikan teks berdasarkan kategori/keyword dari post.
3. **AI draft generator (opsional)** – menghasilkan teks yang lebih kontekstual, tetap dicek oleh validator.[file:1]

### Aturan Fallback

- Jika AI tidak menghasilkan output valid, sistem wajib fallback ke template semi-dinamis.
- Jika template semi-dinamis tidak cocok (tidak ada match), fallback ke template statis.
- Jika seluruh jalur gagal, post tidak di-draft dan diberi status `NEEDS_MANUAL_WRITE`.[file:1]

### Config `ai_prompts.json`

Contoh skema:

```json
{
  "system": {
    "id": "default_id",
    "text": "Kamu adalah asisten engagement yang ramah dan tidak hard-selling. Tugasmu menyiapkan draft balasan singkat dan sopan untuk komentar di Facebook, tanpa janji berlebihan dan tanpa menyertakan link."
  },
  "per_language": {
    "id": {
      "tone": "santai-sopan",
      "max_length": 240
    },
    "en": {
      "tone": "friendly-professional",
      "max_length": 220
    }
  },
  "brand_guidelines": {
    "forbidden_phrases": [
      "dijamin",
      "100% pasti",
      "hasil instan"
    ],
    "preferred_phrases": [
      "boleh diskusi dulu",
      "semoga membantu",
      "kalau berkenan"
    ]
  }
}
```

Prompt AI dibangun dari config ini + konteks post:

```text
[System]
{{system_text}}

[User]
Berikan satu draft balasan singkat (maks {{max_length}} karakter) untuk komentar Facebook berikut:

--- Postingan ---
Teks: {{post_text_snippet}}
Bahasa: {{language}}
Engagement: {{engagement}}
Kategori: {{detected_category}}

Aturan:
- Jangan menyertakan link.
- Hindari klaim berlebihan (garansi, hasil pasti, dsb.).
- Nada: {{tone}}.
- Ajak user untuk lanjut via pesan jika relevan, tapi tanpa memaksa.
```

Output lalu melalui validator (panjang, kata terlarang, fingerprint) sebelum disimpan.

### Aturan Keamanan Draft

- Hindari klaim berlebihan, janji hasil pasti, atau nada terlalu hard-selling.
- Hindari penyertaan link langsung dalam draft awal; link hanya boleh ditambahkan operator.
- Simpan fingerprint hash tiap draft untuk mencegah spam teks yang terlalu mirip.
- Simpan flag keamanan (mis. `contains_sensitive_topic`, `contains_link`) sebagai metadata.[file:1]

---

## 🎯 Targeting & Filter Relevansi

Filter relevansi dan risiko mencakup:

- Keyword whitelist dan blacklist per bahasa.
- Language filter dasar (mis. hanya `id` dan `en`).
- Risk tags (politik, SARA, isu sensitif lain).
- Cooldown per target untuk mencegah scraping berlebihan.
- Duplicate protection berdasarkan `fb_post_id`.
- `health_status` dan `health_score` per target untuk mendeteksi target yang sering error parsing atau menghasilkan data buruk, dikendalikan oleh circuit breaker.

Target dengan error berulang akan turun status ke `DEGRADED` atau `SUSPENDED` hingga lulus health probe.

---

## 🔐 Keamanan & Akses

### Penanganan Secret & Kredensial

- Jangan pernah menyimpan master key/token di repo yang sama dengan konfigurasi umum.
- Gunakan environment variable, secret manager, atau minimal file terproteksi OS.
- Token untuk API pihak ketiga (mis. Graph API) wajib dienkripsi saat at-rest.[file:1]

### Autentikasi & Otorisasi

- Backend menggunakan JWT access + refresh token.
- Role minimal: `viewer`, `operator`, `admin`.
- Endpoint sensitif (settings, approvals, export, user management) hanya dapat diakses role tertentu.[file:1]

### WebSocket Authentication

```text
Client login via REST → menerima access token JWT
    ↓
Client membuka ws: wss://host/ws?token=JWT
    ↓
Server memvalidasi token, expiry, role, dan session version
    ↓
Jika valid → koneksi diterima dan di-subscribe ke channel sesuai role
Jika tidak valid → koneksi ditutup (kode 4401/4403)
```

### Audit Log Minimum

- Login berhasil dan gagal.
- Perubahan konfigurasi dan template.
- Approval, edit, dan reject draft.
- Export data.
- Perubahan role dan status akun pengguna.[file:1]

---

## 🧯 Penanganan Error & Recovery

### Klasifikasi Error

- Authentication / authorization error.
- Rate limit internal.
- Network timeout / DNS error.
- Selector mismatch / parser mismatch.
- Post tidak tersedia / dihapus.
- Database busy / lock timeout.
- Draft generation error (AI / template).
- WebSocket auth / connection error.[file:1]

### Kebijakan Recovery Contoh

| Error                  | Respons awal                             | Eskalasi                                           |
|------------------------|------------------------------------------|----------------------------------------------------|
| Network timeout        | Retry dengan exponential backoff         | Alert Sentry + Telegram jika melewati batas retry |
| Selector mismatch      | Tandai parser degraded                   | Turunkan health_score target, bisa disuspensi      |
| Database busy          | Retry singkat + antrian tulis            | Evaluasi migrasi ke PostgreSQL / tuning indeks     |
| Draft generation error | Fallback ke template lain                | Tandai NEEDS_MANUAL_WRITE jika semua jalur gagal   |
| WebSocket auth error   | Putuskan koneksi                         | Minta login ulang                                  |

### Circuit Breaker per Target

```text
Jika target gagal parsing ≥ N kali dalam window waktu
    ↓ status target → DEGRADED
    ↓ kurangi prioritas di scheduler
Jika tetap gagal hingga ambang kedua
    ↓ status → SUSPENDED (cooldown panjang)
Setelah cooldown
    ↓ lakukan health probe terbatas
    ↓ jika sukses → kembalikan ke ACTIVE
```

### Snapshot & Watchdog

- Simpan snapshot state batch terakhir untuk memudahkan resume.
- Worker memiliki timeout bawaan dan watchdog untuk mencegah hang.
- Dead-man's switch: jika tidak ada aktivitas dalam X menit, kirim alert.[file:1]

---

## 🗄️ Strategi & Skema Database

(… bagian ini sama seperti dokumen awal: skema `targets`, `posts`, `drafts`, `approvals`, `users`, `audit_logs` yang sudah lengkap …)[file:1]

---

## ⚙️ Config File & Skema JSON

### `targets.json`

```json
{
  "targets": [
    {
      "id": "fb_group_x123",
      "name": "Group Jual Beli Kota X",
      "type": "group",
      "url": "https://www.facebook.com/groups/xxxxx",
      "mode": "scrape_public",
      "priority": 10,
      "cooldown_minutes": 30,
      "max_posts_per_run": 50,
      "enabled": true
    }
  ]
}
```

### `keywords.json`

```json
{
  "language": "id",
  "whitelist": [
    "cari jasa",
    "butuh jasa",
    "butuh desain",
    "minta rekomendasi"
  ],
  "blacklist": [
    "politik",
    "sara",
    "hoax",
    "konten dewasa"
  ]
}
```

### `response_templates.json`

(Sama seperti dokumen awal, plus bisa ditambah metadata `category` untuk mendukung template semi-dinamis.)[file:1]

### `scoring_rules.json`

(Sama seperti dokumen awal—mengatur bobot engagement, freshness, relevance, risk_penalty dan threshold queue.)[file:1]

### `feature_flags.json`

(Sama seperti dokumen awal—mengatur `ai_draft_enabled`, `telegram_notifications`, `export_weekly_csv`, dan fitur berisiko tinggi lainnya.)[file:1]

### `rate_limits.json`

Tambah file baru untuk throttling global & per-target:

```json
{
  "global": {
    "max_requests_per_minute": 60,
    "max_requests_per_hour": 1000
  },
  "per_target": {
    "default": {
      "max_requests_per_run": 50,
      "min_interval_seconds": 30
    },
    "overrides": {
      "fb_group_x123": {
        "max_requests_per_run": 30,
        "min_interval_seconds": 60
      }
    }
  },
  "backoff": {
    "on_captcha": {
      "cooldown_minutes": 120
    },
    "on_rate_limit_signal": {
      "cooldown_minutes": 60
    }
  }
}
```

`rate_guard.py` akan membaca file ini dan mengatur bucket global & per-target untuk setiap request ke Facebook.

---

## 📊 Analitik, Feedback Loop & Laporan

Metrik utama yang dilacak, antara lain:[file:1]

- Total post ditemukan per target.
- Total post yang lolos filter dan masuk queue.
- Total draft yang dibuat, di-approve, diedit, dan direject.
- Approval rate per template dan sumber draft (statis, semi-dinamis, AI).
- Jam dan hari dengan peluang tertinggi menemukan kandidat berkualitas.
- Target dengan kualitas tertinggi dan terendah.
- Error rate per target dan per modul.

Tambahan feedback loop:

- Hitung `approval_rate`, `edit_rate`, dan `reject_rate` per template dan per `source_type`.
- Template dengan `reject_rate` tinggi bisa otomatis diberi status `DEGRADED` dan disembunyikan sementara dari Draft Engine sampai direvisi.
- Dashboard menampilkan grafik “Before/After config_version” untuk melihat dampak perubahan `response_templates.json` dan `scoring_rules.json`.

Laporan otomatis:

- **Daily summary** ke Telegram (ringkasan metrik kunci dan error).
- **Weekly CSV export** ke folder `data/exports`.
- **Monthly trend report** untuk analisa strategis.[file:1]

---

## 📱 Monitoring & Notifikasi

Status operasional minimal:[file:1]

| Status    | Arti                                                |
|----------|-----------------------------------------------------|
| RUNNING  | Worker / service aktif bekerja                      |
| IDLE     | Menunggu jadwal / input baru                        |
| DEGRADED | Ada modul/target bermasalah namun belum berhenti    |
| SUSPENDED| Target atau worker dihentikan sementara             |
| STALE    | Heartbeat hilang, perlu pengecekan                  |
| ERROR    | Crash atau fault tak tertangani                     |

Notifikasi dikirim melalui Telegram dan Sentry (issue & alert) berdasarkan aturan yang ditetapkan.

---

## 🧪 Testing Minimum

(Sama seperti dokumen awal, dengan prioritas `test_auth.py`, `test_circuit_breaker.py`, `test_recovery.py`, `test_scorer.py`, `test_draft_engine.py` dan skenario tambahan untuk `rate_guard` dan AI fallback.)[file:1]

---

## ⚠️ Risiko & Mitigasi

(Sama seperti dokumen awal, plus penekanan bahwa scraping Facebook adalah bagian paling rapuh dan sangat bergantung pada perubahan UI dan kebijakan platform.)[file:1]

---

## 📈 Non-Fungsional & SLA

(Sama seperti dokumen awal: target throughput, SLA latency, RTO/RPO, dan monitoring resource.)[file:1]

---

## 🔒 Privasi Data & Retensi

(Sama seperti dokumen awal: batas retensi posts/drafts, audit_logs, dan anonimisasi author_id di laporan analitik.)[file:1]

---

## ⚖️ Kepatuhan Terhadap Kebijakan Facebook

(Blueprint tetap memposisikan sistem sebagai engagement assistant dengan human-in-the-loop, hanya membaca konten publik, tidak menyimpan kredensial FB, dan menghindari high-frequency scraping yang menyerupai serangan.)[file:1]

---

## 🔌 API Contract & Endpoint Utama

(Sama seperti dokumen awal: `/auth/login`, `/drafts/pending`, `/approvals/*`, `/stats/summary`, `/health`, diprefix `/api/v1`.)[file:1]

---

## 🖥️ UX Dashboard & Alur Operator

(Sama seperti dokumen awal: panel dua kolom, highlight keyword, keyboard shortcut `A/R/E/J/K`, badge skor & risiko, serta indikator health target.)[file:1]

---

## 🧾 Versioning Konfigurasi & Rollback

(Setiap perubahan konfigurasi diberi `config_version`, dicatat di `audit_logs`, dan dapat di-rollback via dashboard. Mode canary untuk uji terbatas sebelum rollout penuh.)[file:1]

---

## 🤖 AI Layer: Guardrail & Pengendalian Biaya

(Lapisan AI tetap opsional, dijaga dengan batas panjang prompt/output, daftar kata terlarang, limit jumlah draft AI per jam, score cutoff, dan auto-disable sementara jika terjadi error/rate limit dari provider.)[file:1]

---

## 🛠️ Operasional, Runbook & Lingkungan

(Sama seperti dokumen awal: lingkungan `dev`, `staging`, `prod`, recovery-runbook, dan penggunaan `trace_id/request_id` untuk korelasi log.)[file:1]

---

## 🚀 Deployment

(Sama seperti dokumen awal: docker-compose dengan service `redis`, `db`, `backend`, `worker`, `beat`, `dashboard`, plus checklist sebelum produksi.)[file:1]

---

## 🧭 Fase Implementasi (Solo Dev)

Untuk menghindari over-scope saat implementasi solo, fase berikut direkomendasikan:

1. **Fase 0 – Skeleton & logging**
   - Setup struktur folder, config loader, dan logging + Sentry minimal.
2. **Fase 1 – Backend auth + RBAC + minimal dashboard**
   - Implementasi `auth.py`, tabel `users`, JWT, role, serta dashboard simpel untuk approve/reject dummy draft.
3. **Fase 2 – Scoring engine & config**
   - Implementasi `scorer.py` dan konsumsi `scoring_rules.json` dengan data dummy posts.
4. **Fase 3 – Draft Engine tanpa AI**
   - Implementasi template statis + semi-dinamis, lengkap dengan fallback dan validator.
5. **Fase 4 – Collector (scrape/api) + rate_guard**
   - Implementasi Playwright, `collector.py`, `parser.py`, `detector.py`, `rate_guard.py`, circuit breaker.
6. **Fase 5 – AI Layer (opsional)**
   - Tambah `ai_prompts.json`, integrasi LLM, guardrail, dan limit biaya.

Dengan fase ini, blueprint tidak hanya matang secara desain, tapi juga realistis untuk dikerjakan bertahap oleh satu developer.[file:1]
