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

echo "==> Preparing Python environment"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
pip install -r requirements.txt

echo
echo "==> Writing .env for jCloud deployment"

prompt_default APP_MODE "APP_MODE" "production"
prompt_default ADMIN_NAME "ADMIN_NAME" "Admin"
prompt_default ADMIN_AFFILIATION "ADMIN_AFFILIATION" "Your Lab"
prompt_default ADMIN_EMAIL "ADMIN_EMAIL" "admin@example.com"

DEFAULT_SECRET="$(
python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
read -r -p "APP_SECRET_KEY [press Enter to auto-generate]: " APP_SECRET_KEY
APP_SECRET_KEY="${APP_SECRET_KEY:-$DEFAULT_SECRET}"

read -r -p "SUPABASE_URL [optional]: " SUPABASE_URL
read -r -p "SUPABASE_SECRET_KEY [optional]: " SUPABASE_SECRET_KEY

if [ -n "$SUPABASE_URL" ] && [ -n "$SUPABASE_SECRET_KEY" ]; then
  prompt_default SUPABASE_LOGS_ENABLED "SUPABASE_LOGS_ENABLED" "1"
else
  SUPABASE_LOGS_ENABLED="0"
fi

if [ -f ".env" ]; then
  cp .env ".env.bak.$(date +%Y%m%d%H%M%S)"
fi

cat > .env <<EOF
APP_MODE=$APP_MODE
APP_SECRET_KEY=$APP_SECRET_KEY
ADMIN_NAME=$ADMIN_NAME
ADMIN_AFFILIATION=$ADMIN_AFFILIATION
ADMIN_EMAIL=$ADMIN_EMAIL
SUPABASE_URL=$SUPABASE_URL
SUPABASE_SECRET_KEY=$SUPABASE_SECRET_KEY
SUPABASE_LOGS_ENABLED=$SUPABASE_LOGS_ENABLED
EOF

chmod 600 .env

echo
echo "Done. Wrote $REPO_ROOT/.env"
echo "Next: bash scripts/jcloud_smoke_test.sh"
