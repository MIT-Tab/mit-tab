import csv
from io import TextIOWrapper

from django.db import transaction

from mittab.apps.tab.models import Debater, School


def import_apda_ids_from_csv(uploaded_file, model, name_field, id_field):
    text_file = TextIOWrapper(uploaded_file.file, encoding="utf-8-sig", newline="")
    reader = csv.reader(text_file)

    updated_count = 0
    ignored_count = 0

    try:
        next(reader, None)
        with transaction.atomic():
            for row in reader:
                if len(row) < 2:
                    ignored_count += 1
                    continue

                object_name = row[0].strip()
                object_id = row[1].strip()
                if not object_name:
                    ignored_count += 1
                    continue

                try:
                    apda_id = int(object_id)
                except (TypeError, ValueError):
                    ignored_count += 1
                    continue

                updated = model.objects.filter(**{name_field: object_name}).update(
                    **{id_field: apda_id}
                )
                if updated:
                    updated_count += updated
                else:
                    ignored_count += 1
    finally:
        text_file.detach()

    return updated_count, ignored_count


def import_apda_board_schools_csv(uploaded_file):
    return import_apda_ids_from_csv(uploaded_file, School, "name", "apda_id")


def import_apda_board_debaters_csv(uploaded_file):
    return import_apda_ids_from_csv(uploaded_file, Debater, "name", "apda_id")
