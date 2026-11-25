#!/usr/bin/env bash
# Run on the Ubuntu 24.04 server as root.
# Usage: bash setup-server.sh /path/to/perfect_pullup
set -euo pipefail

PROJECT_SRC="${1:-$(pwd)}"
DOMAIN="gradio.pisarev-n8n.ru"
APP_DIR="/opt/perfect_pullup"
EMAIL="admin@${DOMAIN}"

echo "==> Installing Docker if needed"
if ! command -v docker >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
  systemctl enable --now docker
fi

echo "==> Copying project to ${APP_DIR}"
mkdir -p "$APP_DIR"
cp -r "$PROJECT_SRC"/. "$APP_DIR"/
rm -rf "$APP_DIR/.venv" "$APP_DIR/app/__pycache__"

echo "==> Building and starting container perfect_pull-up"
cd "$APP_DIR/deploy"
docker compose up -d --build
docker ps --filter name=perfect_pull-up --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

echo "==> Installing Nginx and Certbot"
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y nginx certbot python3-certbot-nginx

echo "==> Configuring Nginx for ${DOMAIN}"
cat > /etc/nginx/sites-available/gradio <<NGINX
server {
    listen 80;
    server_name ${DOMAIN};

    location / {
        proxy_pass http://127.0.0.1:7860;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        client_max_body_size 20m;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/gradio /etc/nginx/sites-enabled/gradio
# Do NOT remove the default site: other projects on this server rely on it.
nginx -t && systemctl reload nginx

echo "==> Issuing SSL certificate with auto-renewal"
certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos -m "${EMAIL}" --redirect || true
systemctl enable certbot.timer
systemctl start certbot.timer
echo "Auto-renewal check:"
certbot renew --dry-run || true

echo "==> Done. Open https://${DOMAIN}"
