from mittab.apps.tab.models import TabSettings


def offset_to_quotient(offset):
    return 2 ** offset


def get_varsity_to_novice_ratio():
    varsity_teams = TabSettings.get("novice_outrounds_start_at", None)

    if varsity_teams is None:
        varsity_teams = TabSettings.get("first_novice_outround_matches_varsity", None)

    if varsity_teams is None:
        old_offset = TabSettings.get("var_to_nov", 2)
        varsity_teams = (2 ** old_offset) * 2

    return varsity_teams // 2
