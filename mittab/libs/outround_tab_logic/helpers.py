from mittab.apps.tab.models import TabSettings


def offset_to_quotient(offset):
    return 2 ** offset


def _next_power_of_two(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 1

    if value < 1:
        return 1

    return 1 << (value - 1).bit_length()


def _ratio_from_varsity_target(target, novice_bracket_size):
    try:
        varsity_value = int(target)
    except (TypeError, ValueError):
        return None

    if varsity_value <= 0:
        return None

    ratio = varsity_value // max(1, novice_bracket_size)
    return max(1, ratio)


def get_varsity_to_novice_ratio():
    novice_bracket_size = _next_power_of_two(
        TabSettings.get("nov_teams_to_break", 0)
    )

    for target in (
        TabSettings.get("novice_outrounds_start_at", 0),
        TabSettings.get("first_novice_outround_matches_varsity", 0),
    ):
        ratio = _ratio_from_varsity_target(target, novice_bracket_size)
        if ratio:
            return ratio

    return max(1, offset_to_quotient(TabSettings.get("var_to_nov", 2)))
