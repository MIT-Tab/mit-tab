import os
import socket
import ssl
import sys
from pathlib import Path


def main() -> int:
    host = os.environ.get("MYSQL_HOST", "127.0.0.1")
    if host in {"127.0.0.1", "localhost", ""}:
        return 0

    port = int(os.environ.get("MYSQL_PORT", "3306"))
    ca_path = Path(os.environ.get("MYSQL_SSL_CA", "/var/www/tab/tmp/digitalocean-db-ca.pem"))

    if ca_path.exists() and ca_path.stat().st_size > 0:
        return 0

    try:
        pem = _fetch_certificate(host, port)
    except Exception as exc:  # pragma: no cover - logged and surfaced to caller
        print(f"Failed to retrieve MySQL certificate from {host}:{port}: {exc}", file=sys.stderr)
        return 1

    ca_path.parent.mkdir(parents=True, exist_ok=True)
    ca_path.write_text(pem)
    return 0


def _fetch_certificate(host: str, port: int) -> str:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    with socket.create_connection((host, port), timeout=10) as sock:
        with context.wrap_socket(sock, server_hostname=host) as tls_sock:
            der_cert = tls_sock.getpeercert(binary_form=True)

    return ssl.DER_cert_to_PEM_cert(der_cert)


if __name__ == "__main__":
    sys.exit(main())
