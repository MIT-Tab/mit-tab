#!/usr/bin/env bash
set -e
set +x

log() {
  printf '[start-server] %s\n' "$1" >&2
}

cd /var/www/tab

if [[ -z "$MYSQL_SSL_CA" ]]; then
  if [[ -n "$DATABASE_CA_CERT" && -f "$DATABASE_CA_CERT" ]]; then
    export MYSQL_SSL_CA="$DATABASE_CA_CERT"
    log "Adopting DATABASE_CA_CERT at ${MYSQL_SSL_CA} as MYSQL_SSL_CA."
  elif [[ -n "$MYSQL_CA_CERT" && -f "$MYSQL_CA_CERT" ]]; then
    export MYSQL_SSL_CA="$MYSQL_CA_CERT"
    log "Falling back to MYSQL_CA_CERT at ${MYSQL_SSL_CA}."
  else
    export MYSQL_SSL_CA="/var/www/tab/tmp/digitalocean-db-ca.pem"
    log "Defaulting MYSQL_SSL_CA to ${MYSQL_SSL_CA} (no provider env vars set)."
  fi
else
  log "MYSQL_SSL_CA already defined at ${MYSQL_SSL_CA}; skipping overrides."
fi

log "Invoking ensure_mysql_ca.py with MYSQL_HOST=${MYSQL_HOST:-unset} MYSQL_PORT=${MYSQL_PORT:-unset}."
python -m mittab.scripts.ensure_mysql_ca

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
