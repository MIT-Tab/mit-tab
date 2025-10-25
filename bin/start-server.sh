#!/usr/bin/env bash
set -e
set +x

cd /var/www/tab

# if [[ -z "$MYSQL_SSL_CA" ]]; then
#   export MYSQL_SSL_CA="/var/www/tab/tmp/digitalocean-db-ca.pem"
# fi

# python -m mittab.scripts.ensure_mysql_ca

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
