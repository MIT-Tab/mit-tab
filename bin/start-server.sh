#!/usr/bin/env bash
set -e
set +x

cd /var/www/tab

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
