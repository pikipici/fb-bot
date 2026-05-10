# Deploy runbook

Production target: single Ubuntu VM on `rdpkhorur` (Jakarta). The host
runs Redis locally, serves the FastAPI API + the built React dashboard
directly on port 8100 (no nginx), and schedules scans via
`fb-bot-beat`. SSH-forward to localhost for browser access.

## TL;DR — ship a change

Local → GitHub → server fast-forward → rebuild → restart. Follow this
exact order:

```bash
# local (D:\program\facebook-bot)
git add -A
git commit -m "feat(...): ..."
git push origin main

# server (rdpkhorur)
ssh rdpkhorur "cd /home/ubuntu/fb-bot && git fetch origin \
    && git reset --hard origin/main \
    && source venv/bin/activate \
    && pip install -q -r requirements.txt \
    && cd dashboard && npm install --silent && npm run build \
    && sudo systemctl restart fb-bot-api fb-bot-worker fb-bot-beat \
    && systemctl is-active fb-bot-api fb-bot-worker fb-bot-beat"
```

Verify from local:

```bash
ssh -L 8100:127.0.0.1:8100 rdpkhorur   # or `fbtun` alias
# then in a browser: http://localhost:8100
```

## First-time server setup

Assumes a fresh Ubuntu 22.04+ host.

### 1. Base packages

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip redis-server nodejs npm git
# optional: `sudo apt install sqlite3` for inspecting the DB by hand.
```

Start/enable Redis:

```bash
sudo systemctl enable --now redis-server
redis-cli ping   # expect PONG
```

### 2. Clone + venv

```bash
cd /home/ubuntu
git clone git@github.com:pikipici/fb-bot.git fb-bot
cd fb-bot

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps     # system libs for headless Chromium
```

### 3. Environment

```bash
cp .env.example .env
```

Mandatory keys in `.env`:

| Var                | Notes                                                                  |
| ------------------ | ---------------------------------------------------------------------- |
| `CREDENTIALS_KEY`  | **Fernet key, keep forever.** Regenerating invalidates every stored cookie. Create with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `JWT_SECRET_KEY`   | Random long string. Used to sign access/refresh tokens.                |
| `DATABASE_URL`     | Default `sqlite:///bot/data/app.db` is fine for MVP.                   |
| `ENV`              | `development` or `production` — affects CORS + debug tracebacks.       |
| `CELERY_BROKER_URL`| `redis://127.0.0.1:6379/0` (default).                                  |

### 4. DB bootstrap + admin user

```bash
source venv/bin/activate
PYTHONPATH=. python -c "from server.database import init_db; init_db()"

# Seed admin — if users table is empty:
PYTHONPATH=. python scripts/seed_admin_user.py   # if present
# OR register via the running API after step 5:
# curl -X POST http://127.0.0.1:8100/api/v1/auth/register \
#   -H 'Content-Type: application/json' \
#   -d '{"username":"admin","password":"admin123","role":"admin"}'
```

### 5. systemd services

```bash
sudo cp deploy/systemd/fb-bot-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable fb-bot-api fb-bot-worker fb-bot-beat
sudo systemctl start  fb-bot-api fb-bot-worker fb-bot-beat
systemctl is-active   fb-bot-api fb-bot-worker fb-bot-beat
```

Services run as `ubuntu`, WorkingDirectory `/home/ubuntu/fb-bot`, load
`.env` for env vars. API binds `0.0.0.0:8100` and also serves
`dashboard/dist/` as static. No nginx.

### 6. Build dashboard

```bash
cd dashboard
npm install --silent
npm run build       # outputs dashboard/dist/
cd ..
sudo systemctl restart fb-bot-api
```

### 7. First login + FB account setup

Open `http://localhost:8100` via SSH tunnel, log in as `admin`, head to
`/accounts`, click "Add account", paste cookies via the
Cookie-Editor extension export for `facebook.com`. The cookies are
Fernet-encrypted before insert. Status flips to `ACTIVE` on successful
login check.

## Day-to-day ops

### Tail logs

```bash
journalctl -u fb-bot-api    -f --no-pager -n 100
journalctl -u fb-bot-worker -f --no-pager -n 100
journalctl -u fb-bot-beat   -f --no-pager -n 100
```

### Restart only API (after UI rebuild)

```bash
sudo systemctl restart fb-bot-api
```

### Restart everything (after requirements.txt or model schema change)

```bash
sudo systemctl restart fb-bot-api fb-bot-worker fb-bot-beat
```

### Inspect DB by hand

```bash
sqlite3 /home/ubuntu/fb-bot/bot/data/app.db
sqlite> .tables
sqlite> SELECT id, status, author_name, substr(post_url, 1, 60)
        FROM trending_posts ORDER BY id DESC LIMIT 20;
sqlite> SELECT id, status, sent_at, substr(comment_text, 1, 40)
        FROM comment_history ORDER BY id DESC LIMIT 20;
```

### Run tests on the server

```bash
cd /home/ubuntu/fb-bot
source venv/bin/activate
PYTHONPATH=. python -m pytest --tb=short
```

### Hotfix a stuck SKIPPED / COMMENTED row

```sql
UPDATE trending_posts SET status='NEW' WHERE id=123;
-- then reload /trending
```

## Rollback

```bash
cd /home/ubuntu/fb-bot
git log --oneline -n 5               # find last-good SHA
git reset --hard <sha>
cd dashboard && npm run build && cd ..
sudo systemctl restart fb-bot-api fb-bot-worker fb-bot-beat
```

If a DB migration is involved (future), dump first:

```bash
sqlite3 bot/data/app.db ".backup '/tmp/app_backup_$(date +%F).db'"
```

## Known operational hazards

### `CREDENTIALS_KEY` rotation bricks cookies

If the Fernet key in `.env` changes, every cookie jar in `fb_accounts`
becomes undecryptable and every FB account goes dark. Don't rotate it
unless you're prepared to re-login every account manually. If it
happens by accident, restore the previous key from backup.

### Celery Beat schedule duplication

Running two beat processes pointed at the same Redis broker causes
every task to fire twice. Only one `fb-bot-beat` service per host.

### Browser/Playwright disk bloat

Headless Chromium caches grow. `/home/ubuntu/.cache/ms-playwright` can
be pruned when switching Playwright versions — just re-run
`playwright install chromium` after.

### Unicode cookies

FB rotates cookie names occasionally. If a send suddenly returns
`CookieExpiredError` for every account after an FB update, re-check
required cookie names in `bot/modules/fb_session.py` against a fresh
Cookie-Editor export.

## Monitoring (lightweight)

There's no Prometheus / Sentry yet. Basic health checks:

```bash
curl -sS http://127.0.0.1:8100/api/v1/health/ | jq
# expect {"status":"ok", ...}

systemctl is-active fb-bot-api fb-bot-worker fb-bot-beat
# expect three `active` lines
```

Sentry / Grafana integration is on the roadmap — see
[ARCHITECTURE.md](./ARCHITECTURE.md) § "What's not here (yet)".
