import csv

from django.http import HttpResponse

from mittab.apps.tab.models import Debater, School


def apda_board_csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv")
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    response["Content-Disposition"] = f"attachment; filename={filename}"
    return response


def export_apda_board_schools_csv():
    rows = School.objects.order_by("name").values_list("name", "apda_id")
    return apda_board_csv_response(
        "apda_board_schools.csv",
        ["school", "id"],
        rows,
    )


def export_apda_board_debaters_csv():
    rows = Debater.objects.order_by("name").values_list("name", "apda_id")
    return apda_board_csv_response(
        "apda_board_debaters.csv",
        ["debater", "id"],
        rows,
    )
