#!/usr/bin/env bash
set -e
set +x

cd /var/www/tab

ensure_mysql_ca() {
  python - <<'PY'
import os
import socket
import ssl
import sys

host = os.environ.get("MYSQL_HOST")
port = int(os.environ.get("MYSQL_PORT", "3306"))
ca_path = os.environ.get("MYSQL_SSL_CA", "/var/www/tab/tmp/digitalocean-db-cert.pem")

if not host:
    sys.exit(0)

context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
context.check_hostname = False
context.verify_mode = ssl.CERT_NONE

try:
    with socket.create_connection((host, port), timeout=10) as sock:
        with context.wrap_socket(sock, server_hostname=host) as ssock:
            der_cert = ssock.getpeercert(True)
except Exception as exc:
    print(f"Failed to fetch MySQL certificate: {exc}", file=sys.stderr)
    sys.exit(1)

pem_cert = ssl.DER_cert_to_PEM_cert(der_cert)

os.makedirs(os.path.dirname(ca_path), exist_ok=True)
with open(ca_path, "w") as cert_file:
    cert_file.write(pem_cert)

print(ca_path, end="")
PY
}

MYSQL_SSL_MODE=${MYSQL_SSL_MODE:-REQUIRED}
if [[ -z "$MYSQL_SSL_CA" || ! -s "$MYSQL_SSL_CA" ]]; then
  MYSQL_SSL_CA=$(ensure_mysql_ca)
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
