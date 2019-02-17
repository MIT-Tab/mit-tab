from django.contrib.auth.decorators import permission_required
from django.http import HttpResponse
from django.shortcuts import render

from mittab.libs.data_import import export_xls_files


def export_xls_portal(request):
    """ Export portal, displays links to be used """
    return render(request, 'export_links.html')


@permission_required('tab.tab_settings.can_change', login_url="/403/")
def export_team_xls(request):
    response = HttpResponse(content_type='application/vnd.ms-excel')
    response['Content-Disposition'] = 'attachment; filename=mittab-teams.xls'

    book = export_xls_files.export_teams()
    book.save(response)
    return response


@permission_required('tab.tab_settings.can_change', login_url="/403/")
def export_judge_xls(request):
    response = HttpResponse(content_type='application/vnd.ms-excel')
    response['Content-Disposition'] = 'attachment; filename=mittab-judges.xls'

    book = export_xls_files.export_judges()
    book.save(response)
    return response


@permission_required('tab.tab_settings.can_change', login_url="/403/")
def export_room_xls(request):
    response = HttpResponse(content_type='application/vnd.ms-excel')
    response['Content-Disposition'] = 'attachment; filename=mittab-rooms.xls'

    book = export_xls_files.export_rooms()
    book.save(response)
    return response


@permission_required('tab.tab_settings.can_change', login_url="/403/")
def export_team_stats_xls(request):
    response = HttpResponse(content_type='application/vnd.ms-excel')
    response['Content-Disposition'] = 'attachment; filename=mittab-team-stats.xls'

    book = export_xls_files.export_team_stats()
    book.save(response)
    return response


@permission_required('tab.tab_settings.can_change', login_url="/403/")
def export_debater_stats_xls(request):
    response = HttpResponse(content_type='application/vnd.ms-excel')
    response['Content-Disposition'] = 'attachment; filename=mittab-debater-stats.xls'

    book = export_xls_files.export_debater_stats()
    book.save(response)
    return response