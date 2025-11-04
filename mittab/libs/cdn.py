import json
import logging
import os
import threading

import requests

logger = logging.getLogger(__name__)

_CDN_ENDPOINT_ID = os.environ.get("DIGITALOCEAN_CDN_ENDPOINT_ID")
_CDN_API_TOKEN = os.environ.get("DIGITALOCEAN_API_TOKEN")
_CDN_API_URL_TEMPLATE = (
    "https://api.digitalocean.com/v2/cdn/endpoints/{endpoint_id}/cache"
)


def _purge(paths):
    if not (_CDN_ENDPOINT_ID and _CDN_API_TOKEN):
        logger.info("CDN purge skipped (no credentials): %s", paths)
        return

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
    except requests.RequestException as exc:  # pragma: no cover - network call
        logger.warning("DigitalOcean CDN purge failed: %s", exc)


def purge_cdn_paths(paths):
    """
    Issue an asynchronous purge for the provided list of CDN paths.

    Silently no-ops if CDN credentials are not configured.
    """

    if not paths:
        return

    if not (_CDN_ENDPOINT_ID and _CDN_API_TOKEN):
        return

    thread = threading.Thread(target=_purge, args=(paths,), daemon=True)
    thread.start()
