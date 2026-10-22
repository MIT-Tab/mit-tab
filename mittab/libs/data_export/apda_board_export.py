import csv

from django.http import HttpResponse

from mittab.apps.tab.models import Debater, School
from mittab.libs.spreadsheets import spreadsheet_response


def apda_board_csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv")
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    response["Content-Disposition"] = f"attachment; filename={filename}"
    return response


def export_apda_board_schools_csv(extension="csv"):
    rows = School.objects.order_by("name").values_list("name", "apda_id")
    if extension != "csv":
        return spreadsheet_response(
            "apda_board_schools", extension, ["school", "id"], rows
        )
    return apda_board_csv_response(
        "apda_board_schools.csv",
        ["school", "id"],
        rows,
    )


def export_apda_board_debaters_csv(extension="csv"):
    rows = Debater.objects.order_by("name").values_list("name", "apda_id")
    if extension != "csv":
        return spreadsheet_response(
            "apda_board_debaters", extension, ["debater", "id"], rows
        )
    return apda_board_csv_response(
        "apda_board_debaters.csv",
        ["debater", "id"],
        rows,
    )
