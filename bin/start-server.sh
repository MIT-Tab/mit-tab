#!/usr/bin/env bash
set -e
set +x

cd /var/www/tab
/usr/local/bin/gunicorn mittab.wsgi:application -w 2 --bind 0.0.0.0:8000 -t 300

(cd /var/www/tab; /usr/local/bin/gunicorn mittab.wsgi:application -w 2 --bind 0.0.0.0:8000 -t 300) &
nginx -g "daemon off;"
