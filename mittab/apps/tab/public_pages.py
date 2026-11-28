"""Shared availability rules for public tournament pages."""

from django.db.models import Q

from mittab.apps.tab.models import (
    BreakingTeam,
    Outround,
    PUBLIC_HOME_PAGE_DEFINITIONS,
    Round,
    TabSettings,
)
from mittab.apps.tab.public_rankings import get_public_display_flags


OUTROUND_VISIBILITY = {
    BreakingTeam.VARSITY: "var_teams_visible",
    BreakingTeam.NOVICE: "nov_teams_visible",
}


def get_public_page_availability():
    """Return whether each built-in public page currently has useful content."""
    display_flags = get_public_display_flags()
    has_inround_pairing = Round.objects.exists()
    current_pairing_released = (
        has_inround_pairing and
        TabSettings.get("pairing_released", 0) == 1
    )
    released_divisions = _released_outround_divisions()

    availability = {
        "released_pairings": has_inround_pairing,
        "missing_ballots": current_pairing_released,
        "submit_e_ballot": True,
        "team_portal": True,
        "judge_list": bool(TabSettings.get("judges_public", 0)),
        "team_list": bool(TabSettings.get("teams_public", 0)),
        "varsity_outrounds": BreakingTeam.VARSITY in released_divisions,
        "novice_outrounds": BreakingTeam.NOVICE in released_divisions,
        "public_team_results": display_flags["team_results"],
        "public_speaker_results": display_flags["speaker_results"],
        "public_ballots": display_flags["ballots"],
        "public_motions": bool(TabSettings.get("motions_enabled", 0)),
    }
    availability["public_tab_cards"] = tab_cards_are_public(
        availability["varsity_outrounds"] or availability["novice_outrounds"]
    )
    return availability


def get_active_public_page_flags():
    """Show available pages relevant to the current tournament phase at home."""
    flags = get_public_page_availability()
    in_outrounds = Outround.objects.exists()
    inrounds_started = TabSettings.get("cur_round", 1) > 1
    for slug in (
        "released_pairings", "missing_ballots", "public_team_results",
        "public_speaker_results", "public_ballots",
    ):
        flags[slug] = flags[slug] and inrounds_started and not in_outrounds
    flags["public_motions"] = flags["public_motions"] and inrounds_started
    return flags


def get_public_navigation_pages():
    """List every available public destination, independent of homepage activity."""
    flags = get_public_page_availability()
    return [
        dict(definition, url=definition["url_path"])
        for definition in PUBLIC_HOME_PAGE_DEFINITIONS
        if flags.get(definition["slug"], False)
    ]


def released_outrounds(round_type):
    """Return the same rounds visible on the public outround pairing page."""
    visibility_key = OUTROUND_VISIBILITY.get(round_type, OUTROUND_VISIBILITY[BreakingTeam.VARSITY])
    minimum_size = TabSettings.get(visibility_key, 256)
    return Outround.objects.filter(
        type_of_round=round_type,
        num_teams__gte=minimum_size,
    )


def _released_outround_divisions():
    # Batch the uncached reads. The oldest setting row wins, as in TabSettings.get.
    thresholds = {}
    settings = TabSettings.objects.filter(key__in=OUTROUND_VISIBILITY.values()).order_by("pk")
    for key, value, text in settings.values_list("key", "value", "value_string"):
        thresholds.setdefault(key, text if text is not None else value)

    released = Q()
    for division, key in OUTROUND_VISIBILITY.items():
        minimum_size = thresholds.get(key)
        released |= Q(type_of_round=division, num_teams__gte=256 if minimum_size is None else minimum_size)
    return set(Outround.objects.filter(released).order_by().values_list("type_of_round", flat=True).distinct())


def tab_cards_are_public(outrounds_public=None):
    """Release cards when either division has a publicly visible outround."""
    if not TabSettings.get("publish_tab_cards_with_first_outround", 1):
        return False
    if outrounds_public is not None:
        return outrounds_public
    return bool(_released_outround_divisions())
