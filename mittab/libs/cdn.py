import json
import logging
import os
import re
import threading

import requests

logger = logging.getLogger(__name__)

# For DigitalOcean Spaces CDN purging:
# The CDN endpoint ID can be derived from the bucket name
# The API token can be the same as AWS_SECRET_ACCESS_KEY if it's a DO Personal Access Token
# 
# Using existing environment variables:
# - AWS_SECRET_ACCESS_KEY as the DigitalOcean API token
# - BACKUP_BUCKET as the CDN endpoint identifier
# - BACKUP_S3_ENDPOINT to derive the CDN URL

# Try to use AWS_SECRET_ACCESS_KEY as DO API token (if it's a Personal Access Token)
_CDN_API_TOKEN = os.environ.get("DIGITALOCEAN_API_TOKEN") or os.environ.get("AWS_SECRET_ACCESS_KEY")

# Try to use BACKUP_BUCKET as endpoint ID, or fall back to explicit setting
_BACKUP_BUCKET = os.environ.get("BACKUP_BUCKET", "")
_CDN_ENDPOINT_ID = os.environ.get("DIGITALOCEAN_CDN_ENDPOINT_ID") or _BACKUP_BUCKET

_CDN_API_URL_TEMPLATE = (
    "https://api.digitalocean.com/v2/cdn/endpoints/{endpoint_id}/cache"
)

# Derive CDN URL from S3 endpoint
_S3_ENDPOINT = os.environ.get("BACKUP_S3_ENDPOINT", "")
_CDN_ENDPOINT = _S3_ENDPOINT.replace(
    ".digitaloceanspaces.com",
    ".cdn.digitaloceanspaces.com"
) if _S3_ENDPOINT else None

if _CDN_ENDPOINT:
    logger.info(f"CDN endpoint derived: {_CDN_ENDPOINT}")
if _CDN_ENDPOINT_ID:
    logger.info(f"CDN endpoint ID: {_CDN_ENDPOINT_ID}")
if _CDN_API_TOKEN:
    logger.info(f"CDN API token configured: {_CDN_API_TOKEN[:20]}..." if len(_CDN_API_TOKEN) > 20 else "***")




def _purge(paths):
    """Internal purge function that returns diagnostic info."""
    if not (_CDN_ENDPOINT_ID and _CDN_API_TOKEN):
        logger.info("CDN purge skipped (no credentials): %s", paths)
        return {
            "status": "skipped",
            "reason": "no_credentials",
            "paths": paths,
        }

    url = _CDN_API_URL_TEMPLATE.format(endpoint_id=_CDN_ENDPOINT_ID)
    headers = {
        "Authorization": f"Bearer {_CDN_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"files": sorted(set(paths))}

    try:
        logger.info("Purging CDN paths: %s", paths)
        response = requests.post(
            url, headers=headers, data=json.dumps(payload), timeout=10
        )
        response.raise_for_status()
        logger.info("CDN purge successful for %d paths", len(paths))
        return {
            "status": "success",
            "paths": paths,
            "status_code": response.status_code,
        }
    except requests.RequestException as exc:  # pragma: no cover - network call
        logger.warning("DigitalOcean CDN purge failed: %s", exc)
        return {
            "status": "error",
            "paths": paths,
            "error": str(exc),
        }


def purge_cdn_paths(paths, blocking=False):
    """
    Issue a purge for the provided list of CDN paths.

    Args:
        paths: List of URL paths to purge from the CDN
        blocking: If True, wait for purge to complete before returning.
                 Use True for critical permission changes to ensure immediate updates.
                 If False (default), purge happens in background thread.

    Returns:
        dict: Diagnostic info about the purge (only when blocking=True)

    Silently no-ops if CDN credentials are not configured.
    """

    if not paths:
        return {"status": "skipped", "reason": "no_paths"}

    if not (_CDN_ENDPOINT_ID and _CDN_API_TOKEN):
        return {"status": "skipped", "reason": "no_credentials"}

    if blocking:
        # Synchronous purge - wait for completion and return result
        return _purge(paths)
    else:
        # Asynchronous purge - fire and forget
        thread = threading.Thread(target=_purge, args=(paths,), daemon=True)
        thread.start()
        return {"status": "async", "paths": paths}
