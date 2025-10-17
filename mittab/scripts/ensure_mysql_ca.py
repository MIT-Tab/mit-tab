import os
import re
import subprocess
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
    if not _openssl_exists():
        raise RuntimeError("openssl CLI not available in runtime image")

    cmd = [
        "openssl",
        "s_client",
        "-starttls",
        "mysql",
        "-showcerts",
        "-servername",
        host,
        "-connect",
        f"{host}:{port}",
    ]

    result = subprocess.run(
        cmd,
        input=b"\n",
        check=True,
        capture_output=True,
    )

    stdout = result.stdout.decode("utf-8", errors="ignore")
    certificates = re.findall(
        r"-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----",
        stdout,
    )

    if not certificates:
        raise RuntimeError("Unable to parse certificate chain from openssl output")

    # Return the last certificate which corresponds to the CA provided by DO.
    return certificates[-1] + "\n"


def _openssl_exists() -> bool:
    return subprocess.call(["which", "openssl"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


if __name__ == "__main__":
    sys.exit(main())
