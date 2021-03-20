#!/usr/bin/env bash
set -e
set +x

if [ "$INITIALIZE_TOURNAMENT" == "yes" ]
then
  python manage.py migrate --no-input
  python manage.py initialize_tourney --tab-password $TAB_PASSWORD '.'
fi

(cd /var/www/tab; /usr/local/bin/gunicorn mittab.wsgi:application -w 2 --bind 0.0.0.0:8000 -t 300) &
nginx -g "daemon off;"
