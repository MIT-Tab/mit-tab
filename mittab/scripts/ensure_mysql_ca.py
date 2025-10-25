import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

"""
Deployment hypothesis
---------------------
In DigitalOcean App Platform (current production target) the managed MySQL
cluster always mounts the CA certificate inside the app container and exposes
the absolute path via `DATABASE_CA_CERT`. If that assumption proves correct we
can delete this entire module and simply point `MYSQL_SSL_CA` at the provided
path during bootstrap. The extra logging below exists to validate (or falsify)
that hypothesis in production so we can collapse down to the single-provider
flow.
"""

_NATIVE_CA_ENV_VARS = (
    "DATABASE_CA_CERT",
    "MYSQL_CA_CERT",
    "MYSQL_SSL_CA_SOURCE",
)

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

    if _sync_from_native_source(ca_path):
        _log("Native provider CA detected and synced successfully.")
        return 0

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


def _sync_from_native_source(target: Path) -> bool:
    """
    Attempt to reuse a CA bundle that the platform already mounted for us.

    DigitalOcean App Platform (and most other PaaS providers) exposes the managed
    database CA certificate as a file and surfaces the path via an environment
    variable. Copying that file when present is less brittle than scraping the
    certificate off the server via openssl every time the container boots.
    """

    for env_key in _NATIVE_CA_ENV_VARS:
        source = os.environ.get(env_key)
        if not source:
            _log(f"{env_key} not set; skipping.")
            continue

        source_path = Path(source)
        if not source_path.exists():
            _log(f"{env_key} points to {source_path}, but the file is missing.")
            continue

        if source_path.stat().st_size == 0:
            _log(f"{env_key} points to {source_path}, but the file is empty.")
            continue

        # If the env var already points to the configured target path we are done.
        if os.path.abspath(source_path) == os.path.abspath(target):
            _log(f"{env_key} already references the desired target {target}; nothing to copy.")
            return True

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target)
        _log(f"Copied CA from {source_path} (env {env_key}) to {target}.")
        return True

    _log("No native CA sources succeeded.")
    return False


def _log(message: str) -> None:
    print(f"{LOG_PREFIX} {message}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
