from django.http import JsonResponse

from mittab.apps.tab.models import Outround, TabSettings
from mittab.libs.api_standings import (
    get_varsity_speaker_awards,
    get_novice_speaker_awards,
    get_varsity_team_placements,
    get_novice_team_placements,
    get_non_placing_teams,
    get_new_debater_data,
    get_new_schools_data
)

def _check_tournament_status():
    """Helper function to check tournament status for API endpoints."""
    if not TabSettings.get("apda_tournament", False):
        return JsonResponse(
            {
                "error": (
                    "Tournament is not sanctioned. Please check and update the "
                    "'apda_tournament' setting if this message is incorrect."
                )
            },
            status=403,
        )

    finals = Outround.objects.filter(num_teams=2)
    if not finals.exists() or any(
            final.victor == Outround.UNKNOWN for final in finals
    ):
        return JsonResponse({"error": "Tournament incomplete"}, status=409)

    if not TabSettings.get("results_published", False):
        return JsonResponse({"error": "Results not published"}, status=423)

    return None

def varsity_speaker_awards_api(request):
    """API endpoint for varsity speaker awards."""
    error_response = _check_tournament_status()
    if error_response:
        return error_response

    return JsonResponse({"varsity_speaker_awards": get_varsity_speaker_awards()})


def novice_speaker_awards_api(request):
    """API endpoint for novice speaker awards."""
    error_response = _check_tournament_status()
    if error_response:
        return error_response

    return JsonResponse({"novice_speaker_awards": get_novice_speaker_awards()})


def varsity_team_placements_api(request):
    """API endpoint for varsity team placements."""
    error_response = _check_tournament_status()
    if error_response:
        return error_response

    return JsonResponse({"varsity_team_placements": get_varsity_team_placements()})


def novice_team_placements_api(request):
    """API endpoint for novice team placements."""
    error_response = _check_tournament_status()
    if error_response:
        return error_response

    return JsonResponse({"novice_team_placements": get_novice_team_placements()})


def non_placing_teams_api(request):
    """API endpoint for non-placing teams."""
    error_response = _check_tournament_status()
    if error_response:
        return error_response

    return JsonResponse({"non_placing_teams": get_non_placing_teams()})


def new_debater_data_api(request):
    """API endpoint for new debater data."""
    error_response = _check_tournament_status()
    if error_response:
        return error_response

    return JsonResponse({"new_debater_data": get_new_debater_data()})


def new_schools_api(request):
    """API endpoint for new schools data."""
    error_response = _check_tournament_status()
    if error_response:
        return error_response

    return JsonResponse({"new_schools": get_new_schools_data()})
