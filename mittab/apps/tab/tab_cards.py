"""Build tab cards in bulk for staff and public displays."""

from django.shortcuts import get_object_or_404

from mittab.apps.tab.models import Debater, RoundStats, TabSettings, Team
from mittab.libs.tab_logic import (
    tot_ranks, tot_ranks_deb, tot_speaks, tot_speaks_deb,
)


def get_tab_cards(team_id=None):
    """Fetch the cards' relations once, then build each card in memory."""
    teams = Team.with_preloaded_relations_for_tab_card()
    if team_id is not None:
        teams = [get_object_or_404(teams, pk=team_id)]
    num_rounds = TabSettings.get("tot_rounds", 5)
    cur_round = TabSettings.get("cur_round", 1)
    return [
        card for team in teams
        if (card := _tab_card_context(team, num_rounds, cur_round)) is not None
    ]


def _tab_card_context(team, num_rounds, cur_round):
    """Preserve tab card scoring, including ironmen, byes, and forfeits."""
    rounds = ([r for r in team.gov_team.all()] +
              [r for r in team.opp_team.all()])
    rounds.sort(key=lambda x: x.round_number)

    debaters = [d for d in team.debaters.all()]
    iron_man = False

    if len(debaters) == 1:
        iron_man = True

    if not debaters:
        return None

    deb1 = debaters[0]
    deb2 = debaters[0] if iron_man else debaters[1]

    round_stats = []
    blank = " "

    for i in range(num_rounds):
        round_stats.append([blank] * 7)

    round_stats_by_round_and_debater_id = dict()

    for debater in team.debaters.all():
        for stat in debater.roundstats_set.all():
            round_number = stat.round.round_number
            debater_id = stat.debater_id

            if round_number not in round_stats_by_round_and_debater_id:
                round_stats_by_round_and_debater_id[round_number] = dict()

            if debater_id not in round_stats_by_round_and_debater_id[round_number]:
                round_stats_by_round_and_debater_id[round_number][debater_id] = []

            round_stats_by_round_and_debater_id[round_number][debater_id].append(stat)

    for round_obj in rounds:
        round_number = round_obj.round_number
        if not 1 <= round_number <= num_rounds:
            continue
        dstat1 = []
        dstat2 = []
        if round_number in round_stats_by_round_and_debater_id:
            dstat1 = round_stats_by_round_and_debater_id[round_number].get(deb1.id, [])
            dstat2 = round_stats_by_round_and_debater_id[round_number].get(deb2.id, [])

        blank_rs = RoundStats(debater=deb1, round=round_obj, speaks=0, ranks=0)

        while len(dstat1) + len(dstat2) < 2:
            # Something is wrong with our data, but we don't want to crash
            dstat1.append(blank_rs)

        if not dstat2 and not dstat1:
            break
        if not dstat2:
            dstat1, dstat2 = dstat1[0], dstat1[1]
        elif not dstat1:
            dstat1, dstat2 = dstat2[0], dstat2[1]
        else:
            dstat1, dstat2 = dstat1[0], dstat2[0]

        index = round_obj.round_number - 1

        round_stats[index][3] = " - ".join(
            [j.name for j in round_obj.judges.all()])
        round_stats[index][4] = (float(dstat1.speaks), float(dstat1.ranks))
        round_stats[index][5] = (float(dstat2.speaks), float(dstat2.ranks))
        round_stats[index][6] = (float(dstat1.speaks + dstat2.speaks),
                                 float(dstat1.ranks + dstat2.ranks))

        if round_obj.gov_team == team:
            round_stats[index][2] = round_obj.opp_team
            round_stats[index][0] = "G"
            if round_obj.victor == 1:
                round_stats[index][1] = "W"
            elif round_obj.victor == 2:
                round_stats[index][1] = "L"
            elif round_obj.victor == 3:
                round_stats[index][1] = "WF"
            elif round_obj.victor == 4:
                round_stats[index][1] = "LF"
            elif round_obj.victor == 5:
                round_stats[index][1] = "AD"
            elif round_obj.victor == 6:
                round_stats[index][1] = "AW"
        elif round_obj.opp_team == team:
            round_stats[index][2] = round_obj.gov_team
            round_stats[index][0] = "O"
            if round_obj.victor == 1:
                round_stats[index][1] = "L"
            elif round_obj.victor == 2:
                round_stats[index][1] = "W"
            elif round_obj.victor == 3:
                round_stats[index][1] = "LF"
            elif round_obj.victor == 4:
                round_stats[index][1] = "WF"
            elif round_obj.victor == 5:
                round_stats[index][1] = "AD"
            elif round_obj.victor == 6:
                round_stats[index][1] = "AW"

    for i in range(min(cur_round - 1, num_rounds)):
        if round_stats[i][6] == blank:
            round_stats[i][6] = (0, 0)
    for i in range(1, min(cur_round - 1, num_rounds)):
        round_stats[i][6] = (round_stats[i][6][0] + round_stats[i - 1][6][0],
                             round_stats[i][6][1] + round_stats[i - 1][6][1])
    bye_rounds = [bye.round_number for bye in team.byes.all()]
    bye_round = bye_rounds[0] if len(bye_rounds) == 1 else None

    # Duplicates Debater 1 for display if Ironman team
    return {
        "team": team,
        "team_name": team.display_backend,
        "debater_1": deb1.name,
        "debater_1_status": Debater.NOVICE_CHOICES[deb1.novice_status][1],
        "debater_2": deb2.name,
        "debater_2_status": Debater.NOVICE_CHOICES[deb2.novice_status][1],
        "round_stats": round_stats,
        "d1st": tot_speaks_deb(deb1),
        "d1rt": tot_ranks_deb(deb1),
        "d2st": tot_speaks_deb(deb2),
        "d2rt": tot_ranks_deb(deb2),
        "ts": tot_speaks(team),
        "tr": tot_ranks(team),
        "bye_round": bye_round
    }
