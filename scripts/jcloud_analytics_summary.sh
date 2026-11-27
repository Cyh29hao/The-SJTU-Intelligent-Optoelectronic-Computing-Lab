#!/usr/bin/env bash
set -euo pipefail

fail() { printf '%s\n' "$*" >&2; exit 1; }

service=lab-web.service
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
override_dir=/etc/systemd/system/lab-web.service.d
override="$override_dir/60-analytics-summary.conf"
expected=$'# Managed by scripts/jcloud_analytics_summary.sh\n[Service]\nEnvironment=SUPABASE_ANALYTICS_SUMMARY_ENABLED=1\n'
site_host=opticompt-lab.sjtu.edu.cn
mode=--enable
if (( $# > 0 )); then mode=$1; fi
[[ $# -le 1 && ( $mode == --enable || $mode == --disable ) ]] ||
  fail 'Usage: sudo bash scripts/jcloud_analytics_summary.sh [--enable|--disable]'
[[ $EUID == 0 ]] || fail 'Run as root or with sudo.'
[[ $(systemctl show -p FragmentPath --value "$service") == /etc/systemd/system/lab-web.service ]] ||
  fail 'The existing lab-web systemd unit was not found at its expected location.'
cd "$root"

if [[ $mode == --enable ]]; then
  [[ -x .venv/bin/python && -f .env ]] || fail 'The site .env or Python virtual environment is missing.'
  .venv/bin/python - <<'PY'
from datetime import datetime
from urllib.parse import urlsplit

import requests
from dotenv import dotenv_values

config = dotenv_values(".env", interpolate=False)
enabled = (config.get("SUPABASE_LOGS_ENABLED") or "").strip().lower()
host = (config.get("SUPABASE_URL") or "").strip().rstrip("/")
key = (config.get("SUPABASE_SECRET_KEY") or "").strip()
if enabled not in {"1", "true", "yes", "on"} or urlsplit(host).scheme != "https" or not key:
    raise SystemExit("Supabase logging is missing or disabled in the site's .env.")
try:
    response = requests.post(
        f"{host}/rest/v1/rpc/opticompt_analytics_summary",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        json={"reference_time": datetime.now().isoformat()},
        timeout=4,
        allow_redirects=False,
    )
    if response.status_code == 404:
        raise SystemExit("Analytics summary was not found. Install the SQL function in Supabase first.")
    if not response.ok:
        raise SystemExit(f"Analytics summary probe returned HTTP {response.status_code}. The service was not changed.")
    summary = response.json()
    if not isinstance(summary, dict) or summary.get("version") != 1 or not all(
        name in summary for name in ("page_totals", "pages", "articles", "resources", "trend")
    ):
        raise SystemExit("The analytics response is incompatible. Check the installed SQL.")
except (requests.RequestException, ValueError) as error:
    raise SystemExit(f"Analytics probe failed: {type(error).__name__}. The service was not changed.")
print("Supabase analytics summary probe passed.")
PY
fi

[[ ! -L $override_dir && ! -L $override ]] || fail 'Check the symbolic link at the analytics override path.'
install -d -m 0755 "$override_dir"
exec 9>"$override_dir/.analytics-summary.lock"
flock -n 9 || fail 'Another analytics configuration change is running.'
was_enabled=0
if [[ -e $override ]]; then
  [[ -f $override ]] && cmp -s "$override" <(printf '%s' "$expected") ||
    fail 'The existing analytics override has different contents. Inspect it before proceeding.'
  was_enabled=1
fi
desired=0
[[ $mode != --enable ]] || desired=1

write_override() {
  if [[ $1 == 1 ]]; then
    local candidate
    candidate=$(mktemp "$override_dir/.analytics-summary.XXXXXXXX")
    if ! printf '%s' "$expected" >"$candidate" || ! chmod 0644 "$candidate" || ! mv -f -- "$candidate" "$override"; then
      rm -f -- "$candidate"
      return 1
    fi
  else
    rm -f -- "$override"
  fi
}

restart_and_check() {
  local expected_mode=$1 code pid actual_mode
  systemctl daemon-reload && systemctl restart "$service" || return 1
  for _ in {1..8}; do
    if systemctl is-active --quiet "$service"; then
      pid=$(systemctl show -p MainPID --value "$service")
      if [[ $pid =~ ^[1-9][0-9]*$ && -r /proc/$pid/environ ]]; then
        actual_mode=0
        grep -zFxq 'SUPABASE_ANALYTICS_SUMMARY_ENABLED=1' "/proc/$pid/environ" && actual_mode=1
        if [[ $actual_mode == "$expected_mode" ]]; then
          code=$(curl --silent --head --noproxy '*' --max-time 2 --resolve "$site_host:80:127.0.0.1" \
            --output /dev/null --write-out '%{http_code}' "http://$site_host/editor/login") || code=''
          [[ $code != 200 ]] || return 0
        fi
      fi
    fi
    sleep 1
  done
  return 1
}

if [[ $desired != "$was_enabled" ]]; then
  write_override "$desired"
fi
if ! restart_and_check "$desired"; then
  printf '%s\n' 'Verification failed. Restoring the previous analytics override and restarting.' >&2
  write_override "$was_enabled" && restart_and_check "$was_enabled" ||
    fail 'The previous override could not be verified. Check journalctl -u lab-web.service.'
  exit 1
fi
printf 'Analytics summary enabled: %s. Local login verified.\n' "$desired"
