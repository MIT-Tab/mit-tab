"""APDA ID imports only update the explicit name/ID fields."""

from django.core.exceptions import ValidationError
from django.db import transaction

from mittab.apps.tab.models import Debater, School
from mittab.libs.spreadsheets import SpreadsheetError, column_mapping, read_rows


def import_apda_ids_from_csv(uploaded_file, model, name_field, id_field):
    kind = "schools" if model is School else "debaters"
    rows, mapping, _extras = column_mapping(kind, read_rows(uploaded_file))
    updated_count = 0
    ignored_count = 0
    with transaction.atomic():
        for number, row in rows:
            name_col, id_col = mapping[0], mapping[1]
            object_name = row[name_col] if name_col < len(row) else ""
            object_id = row[id_col] if id_col < len(row) else ""
            if not object_name:
                raise SpreadsheetError(
                    f"Row {number}: the name column cannot be blank."
                )
            try:
                apda_id = model._meta.get_field(id_field).clean(object_id, None)
            except ValidationError as exc:
                raise SpreadsheetError(
                    f'Row {number}: Value "{object_id}" in column "APDA ID": '
                    + "; ".join(exc.messages)
                ) from exc
            updated = model.objects.filter(**{name_field: object_name}).update(
                **{id_field: apda_id}
            )
            if updated:
                updated_count += updated
            else:
                ignored_count += 1
    return updated_count, ignored_count


def import_apda_board_schools_csv(uploaded_file):
    return import_apda_ids_from_csv(uploaded_file, School, "name", "apda_id")


def import_apda_board_debaters_csv(uploaded_file):
    return import_apda_ids_from_csv(uploaded_file, Debater, "name", "apda_id")
