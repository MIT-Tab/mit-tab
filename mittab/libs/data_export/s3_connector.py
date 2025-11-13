import json
import logging
import os
import threading
from datetime import datetime

from django.conf import settings

from mittab.libs.data_export.tab_card import (
    JSONDecimalEncoder,
    get_all_json_data,
)
from mittab.libs.backup.storage import LocalFilesystem, ObjectStorage

LOG = logging.getLogger(__name__)

RESULTS_FILENAME = "published_results"
RESULTS_SUFFIX = ".json"

if settings.BACKUPS["use_s3"]:
    RESULTS_PREFIX = f"{settings.BACKUPS['prefix']}_results"
    RESULTS_STORAGE = ObjectStorage(
        prefix=RESULTS_PREFIX,
        suffix=RESULTS_SUFFIX,
    )
else:
    RESULTS_PREFIX = os.path.join(settings.BACKUPS["prefix"], "results")
    RESULTS_STORAGE = LocalFilesystem(
        prefix=RESULTS_PREFIX,
        suffix=RESULTS_SUFFIX,
    )


def _generate_payload(tournament_name):
    export_ts = datetime.utcnow().isoformat() + "Z"
    return json.dumps(
        {
            "tournament": tournament_name,
            "exported_at": export_ts,
            "tab_cards": get_all_json_data(),
        },
        indent=2,
        cls=JSONDecimalEncoder,
    ).encode("utf-8")


def export_results_now(tournament_name):
    try:
        RESULTS_STORAGE[RESULTS_FILENAME] = _generate_payload(
            tournament_name
        )
    except Exception:  # pragma: no cover
        LOG.exception("Failed to export published results")


def schedule_results_export(tournament_name):
    worker = threading.Thread(
        target=export_results_now,
        args=(tournament_name,),
        daemon=True,
    )
    worker.start()
