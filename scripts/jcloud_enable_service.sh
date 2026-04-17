#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

prompt_default() {
  local var_name="$1"
  local prompt_text="$2"
  local default_value="$3"
  local value
  read -r -p "$prompt_text [$default_value]: " value
  printf -v "$var_name" '%s' "${value:-$default_value}"
}

if [ ! -d ".venv" ]; then
  echo ".venv is missing. Run: bash scripts/jcloud_bootstrap.sh"
  exit 1
fi

if [ ! -f ".env" ]; then
  echo ".env is missing. Run: bash scripts/jcloud_bootstrap.sh"
  exit 1
fi

prompt_default SERVICE_NAME "systemd service name" "lab-web"
prompt_default SERVER_NAME "nginx server_name" "_"
prompt_default GUNICORN_WORKERS "gunicorn workers" "2"

SOCKET_DIR="/run/${SERVICE_NAME}"
SOCKET_PATH="${SOCKET_DIR}/${SERVICE_NAME}.sock"
SYSTEMD_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
NGINX_SITE_AVAILABLE="/etc/nginx/sites-available/${SERVICE_NAME}.conf"
NGINX_SITE_ENABLED="/etc/nginx/sites-enabled/${SERVICE_NAME}.conf"

echo "==> Ensuring gunicorn is installed in the virtualenv"
# shellcheck disable=SC1091
source .venv/bin/activate
pip install gunicorn

echo "==> Writing systemd unit: ${SYSTEMD_UNIT}"
cat > "${SYSTEMD_UNIT}" <<EOF
[Unit]
Description=Gunicorn service for ${SERVICE_NAME}
After=network.target

[Service]
Type=simple
User=root
Group=www-data
WorkingDirectory=${REPO_ROOT}
EnvironmentFile=${REPO_ROOT}/.env
RuntimeDirectory=${SERVICE_NAME}
RuntimeDirectoryMode=0755
ExecStart=${REPO_ROOT}/.venv/bin/gunicorn --workers ${GUNICORN_WORKERS} --bind unix:${SOCKET_PATH} --umask 007 app:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

echo "==> Writing nginx site: ${NGINX_SITE_AVAILABLE}"
cat > "${NGINX_SITE_AVAILABLE}" <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name ${SERVER_NAME};

    client_max_body_size 25M;

    location / {
        include proxy_params;
        proxy_pass http://unix:${SOCKET_PATH};
        proxy_read_timeout 120s;
    }
}
EOF

rm -f /etc/nginx/sites-enabled/default
ln -sf "${NGINX_SITE_AVAILABLE}" "${NGINX_SITE_ENABLED}"

echo "==> Reloading services"
systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"
nginx -t
systemctl enable nginx
systemctl restart nginx

sleep 2

echo
echo "==> systemd status"
systemctl --no-pager --full status "${SERVICE_NAME}" | sed -n '1,18p'

echo
echo "==> nginx status"
systemctl --no-pager --full status nginx | sed -n '1,12p'

echo
echo "==> Local HTTP probe through nginx"
curl -I http://127.0.0.1

echo
echo "Production service enablement complete."
