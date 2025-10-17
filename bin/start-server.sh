#!/usr/bin/env bash
set -e
set +x

cd /var/www/tab

ensure_mysql_ca() {
  local host="${MYSQL_HOST}"
  local port="${MYSQL_PORT:-3306}"
  local target="${MYSQL_SSL_CA:-/var/www/tab/tmp/digitalocean-db-ca.pem}"

  if [[ -z "$host" ]]; then
    echo ""
    return 0
  fi

  if [[ -s "$target" ]]; then
    echo "$target"
    return 0
  fi

  if ! command -v openssl >/dev/null 2>&1; then
    echo "openssl is required to fetch the MySQL certificate" >&2
    return 1
  fi

  local chain cert
  if ! chain=$(openssl s_client -starttls mysql -showcerts -servername "$host" -connect "$host:$port" </dev/null 2>/dev/null); then
    echo "Failed to fetch MySQL certificate chain via openssl" >&2
    return 1
  fi

  cert=$(printf '%s\n' "$chain" | awk '
    /-----BEGIN CERTIFICATE-----/ {capture=1; current=$0; next}
    /-----END CERTIFICATE-----/ {if (capture) {current=current "\n" $0; certificates[++count]=current; capture=0}}
    capture {current=current "\n" $0}
    END {if (count) print certificates[count]}
  ')

  if [[ -z "$cert" ]]; then
    echo "Unable to parse MySQL CA certificate" >&2
    return 1
  fi

  mkdir -p "$(dirname "$target")"
  printf '%s\n' "$cert" > "$target"
  echo "$target"
}

MYSQL_SSL_MODE=${MYSQL_SSL_MODE:-VERIFY_CA}
if [[ -z "$MYSQL_SSL_CA" || ! -s "$MYSQL_SSL_CA" ]]; then
  if ! MYSQL_SSL_CA=$(ensure_mysql_ca); then
    echo "Unable to obtain MySQL CA certificate; aborting startup." >&2
    exit 1
  fi
fi
export MYSQL_SSL_MODE
export MYSQL_SSL_CA

python manage.py migrate --noinput

tournament_initialized() {
  python - <<'PY'
import os
import sys
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mittab.settings")
django.setup()

from django.db import connection  # noqa: E402

with connection.cursor() as cursor:
    cursor.execute("SHOW TABLES LIKE 'tournament_initialized'")
    exists = cursor.fetchone() is not None

sys.exit(0 if exists else 1)
PY
}

create_flag_table() {
  python - <<'PY'
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mittab.settings")
django.setup()

from django.db import connection  # noqa: E402

with connection.cursor() as cursor:
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS tournament_initialized("
        "id int not null, PRIMARY KEY (id))"
    )
PY
}

# Create a table tournament_intialized to use as a flag indicating the
# tournament has been initialzed
if tournament_initialized; then
  echo "Tournament already initialized, skipping init phase";
else
  echo "Initializing tournament";
  python manage.py initialize_tourney --tab-password $TAB_PASSWORD --first-init;
  create_flag_table
fi

if [[ $TOURNAMENT_NAME == *-test ]]; then
  python manage.py loaddata testing_db;
fi

/usr/local/bin/gunicorn --worker-tmp-dir /dev/shm \
  mittab.wsgi:application -w 2 --bind 0.0.0.0:8000 -t 300
