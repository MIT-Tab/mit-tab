#!/usr/bin/env bash
set -e
set +x

cd /var/www/tab

MYSQL_SSL_MODE=${MYSQL_SSL_MODE:-REQUIRED}
MYSQL_SSL_CA=${MYSQL_SSL_CA:-}

function execute-mysql() {
  local args=(
    "-u" "$MYSQL_USER"
    "-h" "$MYSQL_HOST"
    "-D" "$MYSQL_DATABASE"
    "-P" "$MYSQL_PORT"
    "--password=$MYSQL_PASSWORD"
    "--ssl-mode=$MYSQL_SSL_MODE"
  )

  if [[ -n "$MYSQL_SSL_CA" ]]; then
    args+=("--ssl-ca=$MYSQL_SSL_CA")
  fi

  mysql "${args[@]}" -e "$1"
}

python manage.py migrate --noinput

# Create a table tournament_intialized to use as a flag indicating the
# tournament has been initialzed
if [[ $(execute-mysql "show tables like 'tournament_initialized'") ]]; then
  echo "Tournament already initialized, skipping init phase";
else
  echo "Initializing tournament";
  python manage.py initialize_tourney --tab-password $TAB_PASSWORD --first-init;
  execute-mysql "CREATE TABLE tournament_initialized(id int not null, PRIMARY KEY (id));"
fi

if [[ $TOURNAMENT_NAME == *-test ]]; then
  python manage.py loaddata testing_db;
fi

/usr/local/bin/gunicorn --worker-tmp-dir /dev/shm \
  mittab.wsgi:application -w 2 --bind 0.0.0.0:8000 -t 300
