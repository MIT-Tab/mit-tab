from datetime import date

from django.db.models import Count, Min
from django.http import JsonResponse
from django.utils import timezone

from mittab.apps.tab.live_results import (
    final_inround_completed_at,
    speaker_results_available,
)
from mittab.apps.tab.views.debater_views import get_speaker_rankings
from mittab.apps.tab.models import Debater, Team, Outround, Round, TabSettings
from mittab.libs.cacheing import cache_logic
from mittab.libs.tab_logic.stats import tot_wins


def _get_teams_with_min_rounds(min_rounds=3):
    """Helper function to get teams with minimum number of rounds."""
    return Team.objects.annotate(
        total_rounds=Count("gov_team") + Count("opp_team")
    ).filter(total_rounds__gte=min_rounds).values_list("pk", flat=True)


def _get_team_placements_and_ids(team_type):
    """Return every team whose current outround placement is known."""
    outrounds = Outround.objects.filter(
        type_of_round=team_type
    ).prefetch_related(
        "gov_team", "opp_team", "gov_team__debaters", "opp_team__debaters"
    ).order_by("num_teams")

    placements = {}
    placing_team_ids = set()
    for outround in outrounds:
        if not outround.winner:
            continue
        loser_place = (outround.num_teams // 2) + 1
        winner_place = 1 if outround.num_teams <= 2 else (
            outround.num_teams // 4
        ) + 1
        for team, place in (
                (outround.winner, winner_place),
                (outround.loser, loser_place)):
            placements[team.pk] = (min(place, placements.get(team.pk, (place,))[0]), team)
            placing_team_ids.add(team.pk)

    # Add non-breaking teams with 4 wins
    all_teams = Team.objects.filter(
        break_preference=team_type
    ).prefetch_related("debaters")

    for team in all_teams:
        if tot_wins(team) == 4 and team.pk not in placing_team_ids:
            placements[team.pk] = (999, team)
            placing_team_ids.add(team.pk)

    placement_results = [
        team for _place, team in sorted(
            placements.values(), key=lambda item: (item[0], item[1].pk)
        )
    ]
    formatted_data = [
        [
            {"apda_id": debater.apda_id, "tournament_id": debater.pk}
            for debater in team.debaters.all()
        ]
        for team in placement_results
    ]

    return formatted_data, placing_team_ids


def _serialize_live_debater(debater, team):
    # Hybrid teams carry each debater's own school; fall back to the team's.
    school = debater.school or (team.school if team else None)
    return {
        "apda_id": debater.apda_id,
        "tournament_id": debater.pk,
        "name": debater.name,
        "novice": debater.novice_status == Debater.NOVICE,
        "school": {
            "apda_id": school.apda_id,
            "name": school.name,
        } if school else None,
    }


def _serialize_live_team(team):
    return {
        "tournament_id": team.pk,
        "name": str(team),
        "hybrid": team.hybrid_school_id is not None,
        "debaters": [
            _serialize_live_debater(debater, team) for debater in team.debaters.all()
        ],
    }


def _live_team_results(team_type):
    placements = {}
    outrounds = Outround.objects.filter(
        type_of_round=team_type,
        victor__gt=Outround.UNKNOWN,
    ).select_related(
        "gov_team__school", "gov_team__hybrid_school",
        "opp_team__school", "opp_team__hybrid_school",
    ).prefetch_related(
        "gov_team__debaters__school",
        "opp_team__debaters__school",
    )
    for outround in outrounds:
        if not outround.winner:
            continue
        winner_place = 1 if outround.num_teams <= 2 else outround.num_teams // 4 + 1
        loser_place = outround.num_teams // 2 + 1
        for team, place in ((outround.winner, winner_place), (outround.loser, loser_place)):
            current = placements.get(team.pk)
            if current is None or place < current[0]:
                placements[team.pk] = (place, team)
    return [
        {
            "place": place,
            "division": "varsity" if team_type == Team.VARSITY else "novice",
            "team": _serialize_live_team(team),
        }
        for place, team in sorted(placements.values(), key=lambda item: (item[0], item[1].pk))
    ]


def _live_speaker_results():
    if not speaker_results_available():
        return []
    all_speakers, novice = get_speaker_rankings()
    varsity = [
        entry for entry in all_speakers
        if entry[0].novice_status == Debater.VARSITY
    ]
    rows = []
    for division, entries in (("varsity", varsity[:10]), ("novice", novice[:10])):
        previous_place = None
        previous_tied_forward = False
        for index, entry in enumerate(entries, start=1):
            place = previous_place if previous_tied_forward else index
            tied_forward = entry[4] == "Tie not broken"
            rows.append({
                "place": place,
                "division": division,
                "tie": previous_tied_forward or tied_forward,
                "debater": _serialize_live_debater(entry[0], entry[3]),
            })
            previous_place = place
            previous_tied_forward = tied_forward
    return rows


def _tournament_date():
    """Date results were first entered, so it matches the reviewed import."""
    first_result = Round.objects.aggregate(first=Min("result_entered_at"))["first"]
    if first_result:
        return timezone.localdate(first_result)
    try:
        return date.fromisoformat(str(TabSettings.get("tournament_date", "")))
    except ValueError:
        return timezone.localdate()


def live_results_api(request):
    """Machine-readable live results; calculations remain on the consumer."""
    return JsonResponse(
        _live_results_payload(request.build_absolute_uri("/").rstrip("/"))
    )


# Public and unauthenticated, so bound the cost of speaker ranking per worker.
@cache_logic.cache(seconds=60, stampede=30)
def _live_results_payload(source_url):
    host_value = str(TabSettings.get("host_school", "") or "")
    host_id = None
    if host_value.startswith("apda:"):
        try:
            host_id = int(host_value.split(":", 1)[1])
        except ValueError:
            host_id = None

    held_on = _tournament_date()
    season = held_on.year if held_on.month >= 7 else held_on.year - 1
    completed_at = final_inround_completed_at()
    speakers_available = speaker_results_available()
    team_ids = list(_get_teams_with_min_rounds())
    debaters = Debater.objects.filter(team__pk__in=team_ids).distinct()

    return {
        "schema_version": 1,
        "source_url": source_url,
        "tournament": {
            "name": TabSettings.get("tournament_name", "Tournament"),
            "date": held_on.isoformat(),
            "season": str(season),
            "host_school": {
                "apda_id": host_id,
                "name": TabSettings.get("host_school_name", ""),
            },
            "tournament_type": int(TabSettings.get("tournament_type", 0) or 0),
            "sanctioned": bool(TabSettings.get("sanctioned_apda_tournament", 1)),
            "num_teams": len(team_ids),
            "num_novice_debaters": debaters.filter(
                novice_status=Debater.NOVICE
            ).count(),
        },
        "availability": {
            "speakers": speakers_available,
            "final_inround_completed_at": (
                completed_at.isoformat() if completed_at else None
            ),
        },
        "team_results": (
            _live_team_results(Team.VARSITY) + _live_team_results(Team.NOVICE)
        ),
        "speaker_results": _live_speaker_results(),
        "generated_at": timezone.now().isoformat(),
    }

def varsity_speaker_awards_api(request):
    """API endpoint for varsity speaker awards."""
    varsity_speakers, _ = get_speaker_rankings()
    varsity_speakers = varsity_speakers[:10]
    data = [{"apda_id": result[0].apda_id, "tournament_id": result[0].pk}
            for result in varsity_speakers]
    return JsonResponse({"varsity_speaker_awards": data})


def novice_speaker_awards_api(request):
    """API endpoint for novice speaker awards."""
    _, novice_speakers = get_speaker_rankings()
    novice_speakers = novice_speakers[:10]
    data = [{"apda_id": result[0].apda_id, "tournament_id": result[0].pk}
            for result in novice_speakers]
    return JsonResponse({"novice_speaker_awards": data})


def varsity_team_placements_api(request):
    """API endpoint for varsity team placements."""
    placements, _ = _get_team_placements_and_ids(Team.VARSITY)
    return JsonResponse({"varsity_team_placements": placements})


def novice_team_placements_api(request):
    """API endpoint for novice team placements."""
    placements, _ = _get_team_placements_and_ids(Team.NOVICE)
    return JsonResponse({"novice_team_placements": placements})


def non_placing_teams_api(request):
    """API endpoint for non-placing teams."""
    _, varsity_placing_ids = _get_team_placements_and_ids(Team.VARSITY)
    _, novice_placing_ids = _get_team_placements_and_ids(Team.NOVICE)
    all_placing_team_ids = varsity_placing_ids | novice_placing_ids

    teams = Team.objects.prefetch_related("debaters").exclude(
        pk__in=all_placing_team_ids)
    data = [[{"apda_id": debater.apda_id, "tournament_id": debater.pk}
             for debater in team.debaters.all()]
            for team in teams]

    return JsonResponse({"non_placing_teams": data})


def new_debater_data_api(request):
    """API endpoint for new debater data."""
    teams_with_min_rounds = _get_teams_with_min_rounds()

    data = [
        {
            "name": debater["name"],
            "novice_status": debater["novice_status"],
            "school_id": debater["team__school__apda_id"],
            "school_name": debater["team__school__name"],
            "debater_id": debater["pk"]
        }
        for debater in Debater.objects.filter(apda_id=-1)
        .filter(team__pk__in=teams_with_min_rounds)
        .select_related("team__school")
        .values("name", "novice_status",
                "team__school__apda_id", "team__school__name", "pk")
    ]

    return JsonResponse({"new_debater_data": data})


def new_schools_api(request):
    """API endpoint for new schools data."""
    teams_with_min_rounds = _get_teams_with_min_rounds()

    school_names = set()
    debater_data = (
        Debater.objects.filter(apda_id=-1)
        .filter(team__pk__in=teams_with_min_rounds)
        .select_related("team__school")
        .values("team__school__name", "team__school__apda_id")
    )

    for debater in debater_data:
        if debater["team__school__apda_id"] == -1:
            school_names.add(debater["team__school__name"])

    return JsonResponse({"new_schools": list(school_names)})


def debater_counts_api(request):
    teams_with_min_rounds = list(_get_teams_with_min_rounds())
    team_count = Team.objects.filter(pk__in=teams_with_min_rounds).count()
    novice_count = Debater.objects.filter(
        team__pk__in=teams_with_min_rounds,
        novice_status=Debater.NOVICE,
    ).count()
    varsity_count = Debater.objects.filter(
        team__pk__in=teams_with_min_rounds,
        novice_status=Debater.VARSITY,
    ).count()

    return JsonResponse({
        "debater_counts": {
            "varsity": varsity_count,
            "novice": novice_count,
            "teams": team_count,
        },
    })
