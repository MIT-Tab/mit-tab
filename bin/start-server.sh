#!/usr/bin/env bash
set -euo pipefail
set +x

cd "$(dirname "$0")/.."

host="${MYSQL_HOST:-127.0.0.1}"
port="${MYSQL_PORT:-3306}"
if [[ -n "${MYSQL_CA_CERT:-}" ]]; then
  export MYSQL_SSL_CA
  MYSQL_SSL_CA="$(mktemp "${TMPDIR:-/tmp}/mittab-db-ca.XXXXXXXX")"
  printf '%s\n' "$MYSQL_CA_CERT" > "$MYSQL_SSL_CA"
  unset MYSQL_CA_CERT
elif [[ -z "${MYSQL_SSL_CA:-}" && "$host" == *.db.ondigitalocean.com ]]; then
  # Existing managed tournament deployments provision the CA from the MySQL
  # endpoint at boot. A configured trust root takes precedence.
  if [[ ! "$host" =~ ^[A-Za-z0-9.-]+$ || ! "$port" =~ ^[0-9]+$ ]]; then
    echo "Invalid managed MySQL endpoint." >&2
    exit 1
  fi
  export MYSQL_SSL_CA
  MYSQL_SSL_CA="$(mktemp "${TMPDIR:-/tmp}/mittab-db-ca.XXXXXXXX")"
  if ! { timeout 15 openssl s_client -starttls mysql -showcerts \
           -servername "$host" -connect "$host:$port" </dev/null 2>/dev/null || true; } \
       | openssl crl2pkcs7 -nocrl -certfile /dev/stdin \
       | openssl pkcs7 -print_certs -out "$MYSQL_SSL_CA"; then
    echo "Could not read the managed MySQL certificate chain." >&2
    exit 1
  fi
  if [[ ! -s "$MYSQL_SSL_CA" ]]; then
    echo "The managed MySQL certificate chain is empty." >&2
    exit 1
  fi
fi

if [[ -z "${MITTAB_RUST_LISTEN:-}" ]]; then
  export MITTAB_RUST_LISTEN="0.0.0.0:${PORT:-8000}"
fi

mittab-server init
exec mittab-server serve
