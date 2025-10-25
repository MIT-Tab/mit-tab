#!/usr/bin/env bash
set -e
set +x

cd /var/www/tab

if [[ -z "$MYSQL_SSL_CA" ]]; then
  export MYSQL_SSL_CA="/var/www/tab/tmp/digitalocean-db-ca.pem"
fi

host="${MYSQL_HOST:-127.0.0.1}"
do_token="${DIGITALOCEAN_ACCESS_TOKEN:-${DO_API_TOKEN:-${DOCTL_ACCESS_TOKEN:-}}}"
db_id="${DIGITALOCEAN_DATABASE_ID:-${DO_DATABASE_ID:-${DATABASE_ID:-}}}"
doctl_bin="${DOCTL_BIN:-/usr/local/bin/doctl}"

if [[ "$host" != "127.0.0.1" && "$host" != "localhost" && -n "$host" ]]; then
  mkdir -p "$(dirname "$MYSQL_SSL_CA")"
  tmp_ca="${MYSQL_SSL_CA}.doctl"
  DOCTL_ACCESS_TOKEN="$do_token" "$doctl_bin" databases get-ca "$db_id" --format Certificate --no-header > "$tmp_ca" 2>/dev/null || rm -f "$tmp_ca"
  if [[ -s "$tmp_ca" ]]; then
    mv "$tmp_ca" "$MYSQL_SSL_CA"
  fi
fi

if [[ -z "$TAB_PASSWORD" ]]; then
  echo "TAB_PASSWORD must be set." >&2
  exit 1
fi

python manage.py migrate --noinput

ensure_args=(--tab-password "$TAB_PASSWORD")
if [[ -n "$ENTRY_PASSWORD" ]]; then
  ensure_args+=(--entry-password "$ENTRY_PASSWORD")
fi

python manage.py ensure_tournament_initialized "${ensure_args[@]}"

if [[ $TOURNAMENT_NAME == *-test ]]; then
  python manage.py loaddata testing_db;
fi

/usr/local/bin/gunicorn --worker-tmp-dir /dev/shm \
  mittab.wsgi:application -w 2 --bind 0.0.0.0:8000 -t 300
