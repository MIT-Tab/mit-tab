"""Shared availability rules for public tournament pages."""

from mittab.apps.tab.models import (
    BreakingTeam,
    Outround,
    PublicHomePage,
    Round,
    TabSettings,
)
from mittab.apps.tab.public_rankings import get_public_display_flags


def get_public_page_availability():
    """Return whether each built-in public page currently has useful content."""
    display_flags = get_public_display_flags()
    has_inround_pairing = Round.objects.exists()
    current_pairing_released = (
        has_inround_pairing and
        TabSettings.get("pairing_released", 0) == 1
    )

    return {
        "released_pairings": has_inround_pairing,
        "missing_ballots": current_pairing_released,
        "submit_e_ballot": True,
        "team_portal": True,
        "judge_list": bool(TabSettings.get("judges_public", 0)),
        "team_list": bool(TabSettings.get("teams_public", 0)),
        "varsity_outrounds": _outrounds_are_public(BreakingTeam.VARSITY),
        "novice_outrounds": _outrounds_are_public(BreakingTeam.NOVICE),
        "public_team_results": display_flags["team_results"],
        "public_speaker_results": display_flags["speaker_results"],
        "public_ballots": display_flags["ballots"],
        "public_motions": bool(TabSettings.get("motions_enabled", 0)),
    }


def get_active_public_page_flags():
    """Combine homepage configuration with live publication state."""
    configured_pages = list(
        PublicHomePage.objects.values_list("slug", "is_active")
    )
    if not configured_pages:
        PublicHomePage.ensure_defaults()
        configured_pages = list(
            PublicHomePage.objects.values_list("slug", "is_active")
        )
    configured_slugs = {
        slug for slug, is_active in configured_pages if is_active
    }
    availability = get_public_page_availability()
    return {
        slug: slug in configured_slugs and is_available
        for slug, is_available in availability.items()
    }


def _outrounds_are_public(round_type):
    visibility_key = (
        "nov_teams_visible"
        if round_type == BreakingTeam.NOVICE
        else "var_teams_visible"
    )
    visible_at_or_below = TabSettings.get(visibility_key, 256)
    return Outround.objects.filter(
        type_of_round=round_type,
        num_teams__gte=visible_at_or_below,
    ).exists()
