#!/usr/bin/env bash
set -e
set +x

cd /var/www/tab

# Ensure memcached is running for shared caching
if command -v memcached >/dev/null 2>&1; then
  MEMCACHED_PORT="${MEMCACHED_PORT:-11211}"
  MEMCACHED_HOST="${MEMCACHED_HOST:-127.0.0.1}"
  MEMCACHED_MEMORY="${MEMCACHED_MEMORY:-64}"

  memcached -u nobody -m "$MEMCACHED_MEMORY" -p "$MEMCACHED_PORT" -l "$MEMCACHED_HOST" &
  MEMCACHED_PID=$!
  trap 'kill "$MEMCACHED_PID"' EXIT
  export MEMCACHED_LOCATION="${MEMCACHED_HOST}:${MEMCACHED_PORT}"
else
  echo "memcached binary not found; public cache will run in local memory only." >&2
fi

host="${MYSQL_HOST:-127.0.0.1}"
cert_path="${MYSQL_SSL_CA:-/var/www/tab/tmp/digitalocean-db-ca.pem}"
mkdir -p "$(dirname "$cert_path")"
openssl s_client \
  -starttls mysql \
  -showcerts \
  -servername "$host" \
  -connect "${host}:${MYSQL_PORT:-3306}" </dev/null \
  | openssl crl2pkcs7 -nocrl -certfile /dev/stdin \
  | openssl pkcs7 -print_certs -out "$cert_path"
export MYSQL_SSL_CA="$cert_path"

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

GUNICORN_WORKERS=${GUNICORN_WORKERS:-2}
GUNICORN_CONNECTIONS=${GUNICORN_CONNECTIONS:-512}

/usr/local/bin/gunicorn mittab.wsgi:application \
  --worker-class gevent \
  --workers "$GUNICORN_WORKERS" \
  --worker-connections "$GUNICORN_CONNECTIONS" \
  --max-requests 2000 \
  --max-requests-jitter 200 \
  --keep-alive 5 \
  --graceful-timeout 30 \
  --timeout 120 \
  --bind 0.0.0.0:8000
