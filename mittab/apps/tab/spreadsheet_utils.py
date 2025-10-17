import json
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import render


class SpreadsheetError(Exception):
    def __init__(self, message: str, *, row: Optional[int] = None, field: Optional[str] = None):
        super().__init__(message)
        self.row = row
        self.field = field


def _callable(value):
    return callable(value)


def _serialize_value(column: Dict[str, Any], obj: Any) -> Any:
    getter = column.get("value_getter")
    if _callable(getter):
        value = getter(obj)
    else:
        attr_name = column.get("model_field", column["name"])
        value = getattr(obj, attr_name, None)

    if value is None:
        return None

    python_type = column.get("python_type")
    if python_type == "int":
        return int(value)
    if python_type == "decimal":
        return str(value)
    if python_type == "bool":
        return bool(value)
    return value


def _serialize_queryset(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    columns = config["columns"]
    queryset_factory = config["queryset"]
    queryset = queryset_factory() if _callable(queryset_factory) else queryset_factory

    rows: List[Dict[str, Any]] = []
    for obj in queryset:
        row = {}
        for column in columns:
            row[column["name"]] = _serialize_value(column, obj)
        rows.append(row)
    return rows


def _row_is_effectively_empty(row: Dict[str, Any], columns: Iterable[Dict[str, Any]]) -> bool:
    for column in columns:
        if column.get("skip_in_validation"):
            continue
        name = column["name"]
        if name == "id":
            continue
        value = row.get(name)
        if value not in (None, ""):
            return False
    return True


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes", "on"}
    return bool(value)


def _parse_numeric(value: Any, python_type: str) -> Optional[Any]:
    if value in (None, ""):
        return None
    if python_type == "int":
        return int(value)
    if python_type == "decimal":
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise SpreadsheetError("Enter a valid number", field=None) from exc
    return value


def _convert_value(column: Dict[str, Any], value: Any) -> Any:
    if column.get("read_only") and column.get("name") != "id":
        return None

    if value in (None, ""):
        if column.get("required"):
            raise SpreadsheetError("This field is required")
        return None

    python_type = column.get("python_type")
    if python_type == "bool":
        converted = _parse_bool(value)
    elif python_type in {"int", "decimal"}:
        converted = _parse_numeric(value, python_type)
    else:
        valid_values = column.get("valid_values")
        if valid_values and str(value) not in {str(v) for v in valid_values}:
            raise SpreadsheetError("Choose a valid option")
        converted = value

    min_value = column.get("min_value")
    max_value = column.get("max_value")

    if converted is not None:
        if python_type == "decimal" and min_value is not None:
            min_value = Decimal(str(min_value))
        if python_type == "decimal" and max_value is not None:
            max_value = Decimal(str(max_value))

        if min_value is not None and converted < min_value:
            raise SpreadsheetError(f"Must be ≥ {min_value}")
        if max_value is not None and converted > max_value:
            raise SpreadsheetError(f"Must be ≤ {max_value}")

    return converted


def _prepare_updates(config: Dict[str, Any], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    columns = config["columns"]
    allow_create = config.get("allow_create", True)

    to_create: List[Dict[str, Any]] = []
    to_update: List[Dict[str, Any]] = []
    validation_errors: Dict[int, Dict[str, str]] = {}

    for index, row in enumerate(rows):
        row_position = index + 1
        row_id = row.get("id")

        if not row_id and _row_is_effectively_empty(row, columns):
            continue

        cleaned: Dict[str, Any] = {}
        row_errors: Dict[str, str] = {}

        for column in columns:
            model_field = column.get("model_field", column["name"])
            if column.get("skip_model_field"):
                continue
            if column.get("name") == "id":
                continue
            if column.get("read_only") and column.get("model_field"):
                continue

            value = row.get(column["name"])
            try:
                converted = _convert_value(column, value)
            except SpreadsheetError as exc:
                row_errors[column["name"]] = str(exc)
                continue

            if column.get("python_type") == "int" and converted is not None:
                cleaned[model_field] = int(converted)
            elif column.get("python_type") == "decimal" and converted is not None:
                cleaned[model_field] = Decimal(converted)
            else:
                cleaned[model_field] = converted

        if row_errors:
            validation_errors[row_position] = row_errors
            continue

        if row_id:
            to_update.append({
                "id": int(row_id),
                "data": cleaned,
                "row_index": row_position,
            })
        else:
            if not allow_create:
                validation_errors[row_position] = {
                    "id": "Adding new rows is not available here yet."
                }
            else:
                to_create.append({
                    "data": cleaned,
                    "row_index": row_position,
                })

    return {
        "create": to_create,
        "update": to_update,
        "errors": {
            "rows": validation_errors,
            "non_field_errors": [],
        },
    }


def _apply_changes(config: Dict[str, Any], changes: Dict[str, Any], delete_ids: List[int]) -> Dict[str, Any]:
    model = config["model"]
    errors = deepcopy(changes["errors"])
    row_errors = errors["rows"]
    non_field_errors = errors["non_field_errors"]

    try:
        with transaction.atomic():
            if changes["update"]:
                update_ids = [item["id"] for item in changes["update"]]
                instances = model.objects.select_for_update().filter(id__in=update_ids)
                instance_map = {instance.id: instance for instance in instances}

                for item in changes["update"]:
                    instance = instance_map.get(item["id"])
                    if not instance:
                        row_errors.setdefault(item["row_index"], {})["id"] = "Row no longer exists"
                        continue
                    for field_name, value in item["data"].items():
                        setattr(instance, field_name, value)
                    try:
                        instance.full_clean()
                        instance.save()
                    except ValidationError as err:
                        message_dict = getattr(err, "message_dict", {})
                        messages = {
                            field: "; ".join(values)
                            for field, values in message_dict.items()
                        }
                        if not messages and err.messages:
                            messages = {"non_field_errors": "; ".join(err.messages)}
                        row_errors.setdefault(item["row_index"], {}).update(messages)

            if changes["create"]:
                for item in changes["create"]:
                    try:
                        instance = model(**item["data"])
                        instance.full_clean()
                        instance.save()
                    except ValidationError as err:
                        message_dict = getattr(err, "message_dict", {})
                        messages = {
                            field: "; ".join(values)
                            for field, values in message_dict.items()
                        }
                        if not messages and err.messages:
                            messages = {"non_field_errors": "; ".join(err.messages)}
                        row_errors.setdefault(item["row_index"], {}).update(messages)

            if delete_ids:
                objects_to_delete = list(model.objects.filter(id__in=delete_ids))
                for obj in objects_to_delete:
                    try:
                        obj.delete()
                    except Exception as err:
                        non_field_errors.append(str(err))
    except IntegrityError as err:
        non_field_errors.append(str(err))

    if row_errors or non_field_errors:
        return {
            "success": False,
            "errors": {
                "rows": row_errors,
                "non_field_errors": non_field_errors,
            },
        }

    data = _serialize_queryset(config)
    return {
        "success": True,
        "rows": data,
    }


def spreadsheet_view(request, config: Dict[str, Any]):
    if request.method == "POST":
        payload = json.loads(request.body or "{}")
        rows = payload.get("rows", [])
        deleted_ids = payload.get("deleted_ids", [])
        changes = _prepare_updates(config, rows)

        initial_rows = payload.get("rows", [])
        existing_ids = {
            int(row["id"]) for row in initial_rows if str(row.get("id")).isdigit()
        }
        deleted_from_payload = {
            int(pk) for pk in deleted_ids if str(pk).isdigit()
        }
        # Ensure deletes capture removed rows even if client omitted them
        current_ids = {
            int(row["id"]) for row in rows if str(row.get("id")).isdigit()
        }
        missing_ids = existing_ids - current_ids
        delete_ids = list(deleted_from_payload | missing_ids)

        response = _apply_changes(config, changes, delete_ids)
        status = 200 if response.get("success") else 400
        return JsonResponse(response, status=status)

    data = _serialize_queryset(config)

    sanitized_columns = []
    for column in config["columns"]:
        column_copy = {
            key: value for key, value in column.items()
            if key not in {"value_getter", "model_field", "skip_model_field", "python_type", "read_only"}
        }
        column_copy["readOnly"] = column.get("read_only", False)
        sanitized_columns.append(column_copy)

    context = {
        "title": config.get("title", "Spreadsheet"),
        "columns": sanitized_columns,
        "data": data,
        "allow_create": config.get("allow_create", True),
    }
    return render(request, "common/spreadsheet_edit.html", context)
