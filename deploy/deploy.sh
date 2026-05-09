#!/bin/bash
# FB Bot Deploy Script
# Run on server: bash deploy/deploy.sh

set -e

PROJECT_DIR="/home/ubuntu/fb-bot"
VENV="$PROJECT_DIR/venv"

echo "=== FB Bot Deploy ==="

cd "$PROJECT_DIR"

# 1. Pull latest code
echo "[1/8] Pulling latest code..."
git pull origin main

# 2. Install/update dependencies
echo "[2/8] Installing dependencies..."
source "$VENV/bin/activate"
pip install -r requirements.txt --quiet

# 3. Install Playwright browsers (if not already)
echo "[3/8] Ensuring Playwright browsers..."
playwright install chromium 2>/dev/null || true

# 4. Run Alembic migrations
echo "[4/8] Running database migrations..."
alembic upgrade head

# 5. Build dashboard
echo "[5/8] Building dashboard..."
if [ -d "dashboard" ] && [ -f "dashboard/package.json" ]; then
    cd dashboard
    npm install --silent
    npm run build
    cd "$PROJECT_DIR"
else
    echo "  Dashboard not found, skipping..."
fi

# 6. Copy systemd services
echo "[6/8] Installing systemd services..."
sudo cp deploy/systemd/fb-bot-api.service /etc/systemd/system/
sudo cp deploy/systemd/fb-bot-worker.service /etc/systemd/system/
sudo cp deploy/systemd/fb-bot-beat.service /etc/systemd/system/
sudo systemctl daemon-reload

# 7. Copy nginx config
echo "[7/8] Installing nginx config..."
sudo cp deploy/nginx/fb-bot.conf /etc/nginx/sites-available/fb-bot
sudo ln -sf /etc/nginx/sites-available/fb-bot /etc/nginx/sites-enabled/fb-bot
sudo nginx -t && sudo systemctl reload nginx

# 8. Restart services
echo "[8/8] Restarting services..."
sudo systemctl restart fb-bot-api
sudo systemctl restart fb-bot-worker
sudo systemctl restart fb-bot-beat

sudo systemctl enable fb-bot-api
sudo systemctl enable fb-bot-worker
sudo systemctl enable fb-bot-beat

echo ""
echo "=== Deploy Complete ==="
echo "API:       http://localhost:8100"
echo "Dashboard: http://fb-bot.local"
echo ""
echo "Check status:"
echo "  sudo systemctl status fb-bot-api"
echo "  sudo systemctl status fb-bot-worker"
echo "  sudo systemctl status fb-bot-beat"
echo "  sudo journalctl -u fb-bot-api -f"
