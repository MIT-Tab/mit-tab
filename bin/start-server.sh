#!/usr/bin/env bash
set -e
set +x

python manage.py migrate --no-input
python manage.py initialize_tourney --tab-password $TAB_PASSWORD '.'

(cd /var/www/tab; /usr/local/bin/gunicorn mittab.wsgi:application -w 2 -b :8000 -t 300) &
nginx -g "daemon off;"
