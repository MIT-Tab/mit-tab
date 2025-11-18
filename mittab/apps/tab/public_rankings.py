from enum import IntEnum

from mittab.apps.tab.models import TabSettings


class PublicRankingMode(IntEnum):
    NONE = 0
    TEAM = 1
    LAST_BALLOTS = 2
    ALL_BALLOTS = 3


def get_public_ranking_mode():
    """
    Return the configured public ranking mode.

    Falls back to the legacy rankings_public toggle so existing
    tournaments automatically land on the team rankings mode.
    """
    mode = TabSettings.get("public_ranking_mode", 0)
    if mode is None:
        legacy_enabled = TabSettings.get("rankings_public", 0)
        return PublicRankingMode.TEAM if legacy_enabled else PublicRankingMode.NONE
    return PublicRankingMode(int(mode))


def should_show_public_ballot_scores():
    return bool(TabSettings.get("public_ballot_show_speaks", 0))
