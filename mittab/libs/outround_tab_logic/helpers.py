from typing import Optional, Tuple

from mittab.apps.tab.models import Outround, TabSettings


def get_break_sizes() -> Tuple[int, int]:
    return (
        TabSettings.get("var_teams_to_break", 8),
        TabSettings.get("nov_teams_to_break", 0),
    )


def get_concurrency_ratio() -> Optional[int]:
    nov_break = TabSettings.get("nov_teams_to_break", 0)
    var_round = TabSettings.get("novice_concurrent_var_round", 0)
    if not nov_break or not var_round:
        return None
    if var_round < nov_break or var_round % nov_break != 0:
        return None
    ratio = var_round // nov_break
    if ratio < 1:
        return None
    return ratio


def get_concurrent_round(type_of_round: int,
                         num_teams: int) -> Optional[Tuple[int, int]]:
    """
    Returns (other_type, other_num_teams) if the given round is configured
    to run concurrently with another outround based on the break settings.
    """
    ratio = get_concurrency_ratio()
    if not ratio:
        return None

    if type_of_round == Outround.VARSITY:
        if num_teams % ratio != 0:
            return None
        other_num = num_teams // ratio
        other_type = Outround.NOVICE
    else:
        other_num = num_teams * ratio
        other_type = Outround.VARSITY

    if other_num < 2:
        return None

    return (other_type, other_num)
