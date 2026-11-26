#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077

service=lab-web.service
checkout=/opt/lab-web
config_dir=/etc/lab-web-handoff
override_dir=/etc/systemd/system/lab-web.service.d
env_file=$config_dir/env
override_file=$override_dir/90-admin-handoff.conf
site_host=opticompt-lab.sjtu.edu.cn
origin=http://$site_host

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

mode=enable
if (( $# == 1 )) && [[ $1 == --rollback ]]; then
  mode=rollback
elif (( $# != 0 )); then
  fail 'Usage: sudo bash scripts/jcloud_enable_handoff.sh [--rollback]'
fi

[[ $EUID == 0 ]] || fail 'Run this script as root or with sudo.'
cd "$checkout"
[[ -f .env && -x .venv/bin/python ]] || fail 'The existing .env or Python virtual environment is missing.'
[[ $(systemctl show -p FragmentPath --value "$service") == /etc/systemd/system/lab-web.service ]] ||
  fail 'The existing lab-web.service unit was not found at its expected path.'
[[ ! -L $config_dir && ! -L $override_dir && ! -L $env_file && ! -L $override_file ]] ||
  fail 'An Editor configuration path is a symbolic link. Check the service configuration first.'
install -d -m 0700 "$config_dir"
[[ -d $override_dir ]] || mkdir -m 0755 "$override_dir"
exec 9>"$config_dir/activate.lock"
flock -n 9 || fail 'Another Editor configuration change is in progress.'

if [[ $mode == rollback && ! -e $env_file && ! -e $override_file ]]; then
  printf '%s\n' 'The Editor override is already disabled.'
  exit 0
fi

read_password() {
  local role=$1 password confirmation
  IFS= read -r -s -p "$role password (16 characters or more): " password </dev/tty
  printf '\n' >&2
  IFS= read -r -s -p "Confirm $role password: " confirmation </dev/tty
  printf '\n' >&2
  (( ${#password} >= 16 )) || fail 'Password must be at least 16 characters.'
  [[ $password == "$confirmation" ]] || fail 'Password entries did not match.'
  printf '%s' "$password"
}

restore_file() {
  local backup=$1 destination=$2 replacement
  if [[ ! -f $backup ]]; then
    rm -f -- "$destination"
    return "$?"
  fi
  replacement=$(mktemp "${destination%/*}/.restore-handoff.XXXXXXXX") || return 1
  if ! cp -p -- "$backup" "$replacement" || ! mv -f -- "$replacement" "$destination"; then
    rm -f -- "$replacement"
    return 1
  fi
  return 0
}

finish() {
  local result=$1 restore_failed=0
  trap - EXIT
  trap '' INT TERM
  if (( restore_on_exit )); then
    printf '%s\n' 'Verification failed. Restoring the previous Editor configuration.' >&2
    restore_file "$transaction/env.before" "$env_file" || restore_failed=1
    restore_file "$transaction/override.before" "$override_file" || restore_failed=1
    if (( restore_failed )) ||
       ! systemctl daemon-reload || ! systemctl restart "$service" ||
       ! systemctl is-active --quiet "$service"; then
      printf 'Automatic restoration needs attention. Root-only backups remain in %s\n' "$transaction" >&2
      exit 1
    fi
  fi
  [[ -z $override_candidate ]] || rm -f -- "$override_candidate"
  rm -f -- "$transaction/env.before" "$transaction/override.before" "$transaction/env.new"
  rmdir -- "$transaction"
  exit "$result"
}

origin_request() {
  curl --silent --show-error --head --max-time 5 --noproxy '*' \
    --resolve "$site_host:80:127.0.0.1" --output /dev/null --write-out "$2" "$origin$1"
}

admin_origin_redirect() {
  origin_request /admin '%{http_code} %{redirect_url}'
}

verify_origin_routes() {
  local redirect_target=$1 response
  response=$(admin_origin_redirect) || return 1
  if [[ $response != "302 $origin$redirect_target" ]]; then
    printf '/admin returned %s, expected a redirect to %s.\n' "$response" "$redirect_target" >&2
    return 1
  fi
  response=$(origin_request "$redirect_target" '%{http_code}') || return 1
  if [[ $response != 200 ]]; then
    printf '%s returned HTTP %s, expected 200.\n' "$redirect_target" "$response" >&2
    return 1
  fi
}

transaction=$(mktemp -d "$config_dir/.update.XXXXXXXX")
override_candidate=''
restore_on_exit=0
trap 'finish "$?"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ -e $env_file ]]; then cp -p -- "$env_file" "$transaction/env.before"; fi
if [[ -e $override_file ]]; then cp -p -- "$override_file" "$transaction/override.before"; fi

if [[ $mode == enable ]]; then
  [[ -t 0 ]] || fail 'Run this command in an interactive cloud console or SSH terminal.'
  response=$(admin_origin_redirect) || fail 'Local Nginx is not reachable. The current service configuration was retained.'
  case "$response" in
    "302 $origin/register"|"302 $origin/editor/login") ;;
    *) fail 'Local Nginx did not return the expected admin redirect. The current service configuration was retained.' ;;
  esac
  unset owner_password editor_password
  owner_password=$(read_password owner)
  editor_password=$(read_password editor)
  [[ $owner_password != "$editor_password" ]] || fail 'Choose different passwords for owner and editor.'

  # Pass passwords through an anonymous pipe. Python reads the program from fd 3.
  printf '%s\0%s' "$owner_password" "$editor_password" |
    .venv/bin/python /dev/fd/3 "$transaction/env.before" "$transaction/env.new" 3<<'PY'
import re
import secrets
import sys
from pathlib import Path

from dotenv import dotenv_values
from werkzeug.security import check_password_hash, generate_password_hash

previous_path, output_path = map(Path, sys.argv[1:])
previous = dotenv_values(previous_path, interpolate=False) if previous_path.is_file() else {}
base = dotenv_values('.env', interpolate=False)
owner, editor = sys.stdin.buffer.read().decode('utf-8').split('\0')
values = {
    'APP_MODE': 'production',
    'ADMIN_HANDOFF_PRODUCTION': '1',
    'ADMIN_HANDOFF_HOST': 'opticompt-lab.sjtu.edu.cn',
    'NEWS_EDITOR_PREVIEW': '0',
    'ADMIN_HANDOFF_PREVIEW': '0',
    'NEWS_EDITOR_DB': '/var/lib/lab-web-handoff/news.sqlite3',
}
for role, password in (('OWNER', owner), ('EDITOR', editor)):
    name = f'NEWS_EDITOR_{role}_PASSWORD_HASH'
    current = previous.get(name)
    values[name] = current if current and check_password_hash(current, password) else generate_password_hash(password)

if not (base.get('APP_SECRET_KEY') or '').strip():
    current = previous.get('APP_SECRET_KEY')
    if current and not re.fullmatch(r'[a-f0-9]{64}', current):
        raise SystemExit('Review the existing handoff session key before changing the configuration.')
    values['APP_SECRET_KEY'] = current or secrets.token_hex(32)

output_path.write_text(''.join(f'{name}={value}\n' for name, value in values.items()), encoding='utf-8')
PY
  unset owner_password editor_password

  override_candidate=$(mktemp "$override_dir/.admin-handoff.XXXXXXXX")
  cat >"$override_candidate" <<'UNIT'
[Service]
EnvironmentFile=/etc/lab-web-handoff/env
StateDirectory=lab-web-handoff
StateDirectoryMode=0700
UNIT
  chmod 0644 "$override_candidate"
  restore_on_exit=1
  mv -f -- "$transaction/env.new" "$env_file"
  mv -f -- "$override_candidate" "$override_file"
  redirect_target=/editor/login
else
  restore_on_exit=1
  rm -f -- "$override_file" "$env_file"
  redirect_target=/register
fi

systemctl daemon-reload
systemctl restart "$service"
for attempt in 1 2 3 4; do
  if ! systemctl is-active --quiet "$service"; then
    printf '%s is not active.\n' "$service" >&2
  elif verify_origin_routes "$redirect_target"; then
    restore_on_exit=0
    printf 'Local Nginx and service verified. /admin now leads to %s.\n' "$redirect_target"
    printf 'Verify the public site from a browser outside this host: https://%s/admin\n' "$site_host"
    exit 0
  fi
  if (( attempt < 4 )); then sleep 2; fi
done

fail 'The local Nginx admin routes could not be verified. Check the lab-web.service journal.'
