import os
import re
import subprocess
import sys
from pathlib import Path

LOG_PREFIX = "[ensure_mysql_ca]"


def main() -> int:
    host = os.environ.get("MYSQL_HOST", "127.0.0.1")
    if host in {"127.0.0.1", "localhost", ""}:
        _log(f"MYSQL_HOST resolved to '{host}', assuming local database - no CA work needed.")
        return 0

    port = int(os.environ.get("MYSQL_PORT", "3306"))
    ca_path = Path(os.environ.get("MYSQL_SSL_CA", "/var/www/tab/tmp/digitalocean-db-ca.pem"))
    _log(
        "Starting CA bootstrap with settings: "
        f"MYSQL_HOST={host}, MYSQL_PORT={port}, target CA path={ca_path}"
    )

    if ca_path.exists() and ca_path.stat().st_size > 0:
        _log(f"Existing CA file found at {ca_path} ({ca_path.stat().st_size} bytes); reusing.")
        return 0

    try:
        _log("Falling back to openssl s_client to scrape CA from the remote host.")
        pem = _fetch_certificate(host, port)
    except Exception as exc:  # pragma: no cover - logged and surfaced to caller
        _log(f"Failed to retrieve MySQL certificate from {host}:{port}: {exc}")
        return 1

    ca_path.parent.mkdir(parents=True, exist_ok=True)
    ca_path.write_text(pem)
    _log(f"Wrote CA bundle to {ca_path} ({len(pem)} bytes).")
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

    _log("Running command: " + " ".join(cmd))
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

    _log(f"Extracted {len(certificates)} certificates from openssl output; selecting the last entry.")
    # Return the last certificate which corresponds to the CA provided by DO.
    return certificates[-1] + "\n"


def _openssl_exists() -> bool:
    exists = subprocess.call(["which", "openssl"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0
    if not exists:
        _log("openssl binary not detected in PATH.")
    return exists


def _log(message: str) -> None:
    print(f"{LOG_PREFIX} {message}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
