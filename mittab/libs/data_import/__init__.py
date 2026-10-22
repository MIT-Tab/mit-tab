from abc import ABC, abstractmethod

from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, transaction
from django.forms import ModelForm

from mittab.apps.tab.models import AuditAttributionMixin, TabSettings
from mittab.libs.spreadsheets import (
    LAYOUTS,
    SpreadsheetError,
    column_mapping,
    read_rows,
)

InvalidWorkbookException = SpreadsheetError


class Workbook:
    """Read the first worksheet (or CSV), preserving physical row numbers."""

    def __init__(self, file_to_import):
        self.data = read_rows(file_to_import)
        self.row_numbers = []
        self.named_headers = False

    def configure(self, kind):
        round_count = TabSettings.get("tot_rounds", 5) if kind == "judges" else 0
        rows, mapping, extras = column_mapping(kind, self.data, round_count)
        self.named_headers = rows[0][0] != self.data[0][0]
        self.row_numbers = [number for number, _ in rows]
        width = len(LAYOUTS[kind])
        self.data = [
            [
                row[mapping[i]] if i in mapping and mapping[i] < len(row) else ""
                for i in range(width)
            ]
            + [row[i] if i is not None and i < len(row) else "" for i in extras]
            for _, row in rows
        ]

    def rows(self):
        return iter(self.data)


class WorkbookImporter(ABC):
    kind = None

    def __init__(self, workbook, created_by=None):
        self.workbook = workbook
        self.created_by = created_by
        self.errors = []

    @abstractmethod
    def import_row(self, row, row_number):
        pass

    def import_data(self):
        try:
            if isinstance(self.workbook, Workbook):
                self.workbook.configure(self.kind)
        except SpreadsheetError as exc:
            return [str(exc)]
        with transaction.atomic():
            for row_number, row in enumerate(self.workbook.rows()):
                try:
                    with transaction.atomic():
                        self.import_row(row, row_number)
                except (ValidationError, DataError, IntegrityError) as exc:
                    detail = (
                        "; ".join(exc.messages)
                        if isinstance(exc, ValidationError)
                        else (
                            "A value conflicts with an existing record "
                            "or exceeds a database limit. "
                            "Check the names and column values."
                        )
                    )
                    self.error(detail, row_number)
            if self.errors:
                transaction.set_rollback(True)
        return self.errors

    def create(self, obj):
        if isinstance(obj, ModelForm):
            if isinstance(obj.instance, AuditAttributionMixin):
                obj.save(actor=self.created_by)
            else:
                obj.save()
        else:
            obj.full_clean()
            obj.save()
        return obj

    def form_errors(self, form, columns, row, row_number):
        for field, messages in form.errors.items():
            column = columns.get(field)
            label = LAYOUTS[self.kind][column] if column is not None else field
            value = row[column] if column is not None else ""
            for message in messages:
                self.error(
                    f'Value "{value}" in column "{label}": {message}', row_number
                )

    def error(self, msg, row_number=None):
        if row_number is not None:
            numbers = getattr(self.workbook, "row_numbers", [])
            number = numbers[row_number] if numbers else row_number + 1
            self.errors.append(f"Row {number}: {msg}")
        else:
            self.errors.append(msg)
