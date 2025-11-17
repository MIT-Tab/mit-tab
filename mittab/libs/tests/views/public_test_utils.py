from django.core.cache import caches

from mittab.apps.tab.models import Outround, Room, Round, TabSettings, Team
from mittab.apps.tab.public_rankings import (
    set_ballot_round_settings,
    set_ranking_settings,
)
from mittab.libs.cacheing import cache_logic


def prepare_public_site_state():
    caches["public"].clear()
    cache_logic.clear_cache()
    Team.objects.update(ranking_public=True)

    Outround(
        gov_team=Team.objects.first(),
        opp_team=Team.objects.last(),
        num_teams=2,
        type_of_round=Outround.NOVICE,
        room=Room.objects.first(),
    ).save()
    Outround(
        gov_team=Team.objects.first(),
        opp_team=Team.objects.last(),
        num_teams=2,
        type_of_round=Outround.VARSITY,
        room=Room.objects.last(),
    ).save()

    TabSettings.set("cur_round", 3)
    TabSettings.set("pairing_released", 1)
    TabSettings.set("judges_public", 1)
    TabSettings.set("teams_public", 1)
    TabSettings.set("debaters_public", 1)
    TabSettings.set("tot_rounds", 5)
    TabSettings.set("var_teams_visible", 2)
    TabSettings.set("nov_teams_visible", 2)

    for slug, include_speaks, max_visible in (
        ("team", False, 1000),
        ("varsity", True, 10),
        ("novice", False, 10),
    ):
        set_ranking_settings(slug, public=True, include_speaks=include_speaks, max_visible=max_visible)

    set_ballot_round_settings(1, visible=True, include_speaks=False, include_ranks=False)

    target_round_number = TabSettings.get("cur_round") - 1
    test_round = Round.objects.filter(round_number=target_round_number).first()
    original_victor = test_round.victor
    test_round.victor = Round.NONE
    test_round.save()

    return test_round, original_victor


def reset_public_site_state(test_round, original_victor):
    test_round.victor = original_victor
    test_round.save()
    caches["public"].clear()
    cache_logic.clear_cache()
