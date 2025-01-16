from decimal import Decimal
import json
from mittab.apps.tab.helpers import redirect_and_flash_error
from mittab.apps.tab.models import Bye, RoundStats
from mittab.apps.tab.models import Team, Round, TabSettings, Debater, Bye, RoundStats
from django.db.models import Prefetch

from mittab.libs.tab_logic.stats import tot_ranks, tot_ranks_deb, tot_speaks, tot_speaks_deb

GOV = "G"
OPP = "O"


def get_victor_label(victor_code, side):
    side = 0 if side == GOV else 1
    victor_map = {
        1: ("W", "L"),
        2: ("L", "W"),
        3: ("WF", "LF"),
        4: ("LF", "WF"),
        5: ("AD", "AD"),
        6: ("AW", "AW"),
    }
    return victor_map[victor_code][side]


def get_dstats(round_obj, deb1, deb2, iron_man):
    """Taken from original tab card function.
    Pretty sure there is a good refactor here but seems hard to test and worried about breaking something"""
    dstat1 = [
        k for k in RoundStats.objects.filter(debater=deb1).filter(
            round=round_obj).all()
    ]
    dstat2 = []
    if not iron_man:
        dstat2 = [
            k for k in RoundStats.objects.filter(debater=deb2).filter(
                round=round_obj).all()
        ]
    blank_rs = RoundStats(debater=deb1, round=round_obj, speaks=0, ranks=0)
    while len(dstat1) + len(dstat2) < 2:
        # Something is wrong with our data, but we don't want to crash
        dstat1.append(blank_rs)
    if not dstat2 and not dstat1:
        return None, None
    if not dstat2:
        dstat1, dstat2 = dstat1[0], dstat1[1]
    elif not dstat1:
        dstat1, dstat2 = dstat2[0], dstat2[1]
    else:
        dstat1, dstat2 = dstat1[0], dstat2[0]
    return dstat1, dstat2


def json_get_round(round_obj, team, deb1, deb2):
    chair = round_obj.chair
    json_round = {
        "round_number": round_obj.round_number,
        "round_id": round_obj.pk,
        "side": GOV if round_obj.gov_team == team else OPP,
        "result": get_victor_label(round_obj.victor, GOV if round_obj.gov_team == team else OPP),
        "chair": chair.name,
        "wings": [judge.name for judge in round_obj.judges.all() if judge != chair],
    }

    opponent = round_obj.opp_team if round_obj.gov_team == team else round_obj.gov_team
    if opponent:
        opponent_debaters = list(opponent.debaters.all())
        json_round["opponent"] = {
            "name": opponent.get_or_create_team_code(),
            "school": opponent.school.name,
            "debater1": opponent_debaters[0].name if opponent_debaters else None,
            "debater2": opponent_debaters[1].name if len(opponent_debaters) > 1 else None,
        }

    json_round["debater1"] = list(
        (stat.speaks, stat.ranks)
        for stat in RoundStats.objects.filter(debater=deb1, round=round_obj)
    )
    json_round["debater2"] = list(
        (stat.speaks, stat.ranks)
        for stat in RoundStats.objects.filter(debater=deb2, round=round_obj)
    ) if deb2 else []

    try:
        bye_round = Bye.objects.get(bye_team=team).round_number
        json_round[bye_round - 1][bye_round]
    except Bye.DoesNotExist:
        pass

    return json_round


class JSONDecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return json.JSONEncoder.default(self, obj)


def get_all_json_data():
    all_tab_cards_data = {}

    # Prefetch related data to reduce database queries
    teams = Team.objects.prefetch_related(
        'school',
        Prefetch('debaters', queryset=Debater.objects.all()),
        Prefetch('gov_team', queryset=Round.objects.all()),
        Prefetch('opp_team', queryset=Round.objects.all())
    )

    total_rounds = TabSettings.objects.get(key="tot_rounds").value

    for team in teams:
        tab_card_data = {
            "team_name": team.get_or_create_team_code(), "team_school": team.school.name}

        debaters = list(team.debaters.all())
        deb1 = debaters[0]
        tab_card_data["debater_1"] = deb1.name
        tab_card_data["debater_1_status"] = Debater.NOVICE_CHOICES[deb1.novice_status][1]

        if len(debaters) > 1:
            deb2 = debaters[1]
            tab_card_data["debater_2"] = deb2.name
            tab_card_data["debater_2_status"] = Debater.NOVICE_CHOICES[deb2.novice_status][1]
        else:
            deb2 = None

        rounds = list(team.gov_team.all()) + list(team.opp_team.all())

        round_data = []
        for round_obj in rounds:
            if round_obj.victor != 0:  # Don't include rounds without a result
                round_data.append(json_get_round(round_obj, team, deb1, deb2))
        tab_card_data["rounds"] = round_data

        all_tab_cards_data[team.get_or_create_team_code()] = tab_card_data
    return all_tab_cards_data


def get_tab_card_data(request, team_id):
    try:
        team_id = int(team_id)
    except ValueError:
        return redirect_and_flash_error(request, "Invalid team id")

    # gov_team -> rounds where team is gov, opp_team -> rounds where team is opp
    team = Team.objects.prefetch_related(
        "gov_team", "opp_team", "debaters").get(pk=team_id)
    rounds = (
        list(Round.objects.filter(gov_team=team)) +
        list(Round.objects.filter(opp_team=team))
    )
    rounds.sort(key=lambda x: x.round_number)
    debaters = list(team.debaters.all())
    iron_man = len(debaters) == 1
    deb1 = debaters[0]
    deb2 = deb1 if iron_man else debaters[1]

    num_rounds = TabSettings.objects.get(key="tot_rounds").value
    cur_round = TabSettings.objects.get(key="cur_round").value
    blank = " "
    round_stats = [[blank] * 7 for _ in range(num_rounds)]
    speaksRolling = 0
    ranksRolling = 0
    for round_obj in rounds:
        if round_obj.victor != 0:  # Don't include rounds without a result
            dstat1, dstat2 = get_dstats(round_obj, deb1, deb2, iron_man)
            if not dstat1:
                break
            side = GOV if round_obj.gov_team == team else OPP
            opponent = round_obj.opp_team if round_obj.gov_team == team else round_obj.gov_team
            speaksRolling += float(dstat1.speaks + dstat2.speaks)
            ranksRolling += float(dstat1.ranks + dstat2.ranks)
            round_stats[round_obj.round_number-1] = [side,
                                                     get_victor_label(
                                                         round_obj.victor, side),
                                                     opponent.display_backend,
                                                     " - ".join(
                                                         j.name for j in round_obj.judges.all()),
                                                     (float(dstat1.speaks),
                                                      float(dstat1.ranks)),
                                                     (float(dstat2.speaks),
                                                      float(dstat2.ranks)),
                                                     (float(speaksRolling), float(ranksRolling))]

    for i in range(cur_round - 1):
        # Don't fill in totals for incomplete rounds
        if round_stats[i][6] == blank and blank not in round_stats[i + 1][:5]:
            round_stats[i][6] = (0, 0) if i == 0 else round_stats[i - 1][6]

    try:
        bye_round = Bye.objects.get(bye_team=team).round_number
    except Bye.DoesNotExist:
        bye_round = None

    return {
        "team_school": team.school,
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


def csv_tab_cards(writer):
    # Write the CSV header row
    header = [
        "Team Name", "School", "Round", "Gov/Opp", "Win/Loss",
        "Opponent", "Chair", "Wing(s)", "Debater 1", "N/V", "Speaks", "Ranks", "Debater 2", "N/V", "Speaks", "Ranks", "Total Speaks", "Total Ranks"
    ]
    writer.writerow(header)

    teams = Team.objects.prefetch_related(
        'school',
        'debaters',
        'gov_team',
        'opp_team'
    )
    total_rounds = TabSettings.objects.get(key="tot_rounds").value

    for team in teams:
        debaters = list(team.debaters.all())
        deb1 = debaters[0]
        deb1_status = Debater.NOVICE_CHOICES[deb1.novice_status][1][0]
        iron_man = len(debaters) < 2
        if iron_man:
            deb2 = deb1
            deb2_status = deb1_status
        else:
            deb2 = debaters[1]
            deb2_status = Debater.NOVICE_CHOICES[deb2.novice_status][1][0]

        rounds = list(team.gov_team.all()) + list(team.opp_team.all())
        round_data = [{}] * total_rounds

        for round_obj in rounds:
            side = GOV if round_obj.gov_team == team else OPP
            result = get_victor_label(round_obj.victor, side)
            opponent = round_obj.opp_team if round_obj.gov_team == team else round_obj.gov_team
            opponent_name = opponent.get_or_create_team_code() if opponent else "BYE"
            chair = round_obj.chair.name
            wings = " - ".join(
                judge.name for judge in round_obj.judges.exclude(pk=round_obj.chair.pk))

            dstat1, dstat2 = get_dstats(
                round_obj, deb1, deb2, len(debaters) == 1)
            round_data[round_obj.round_number - 1] = [
                round_obj.round_number,
                side,
                result,
                opponent_name,
                chair,
                wings,
                deb1.name,
                deb1_status,
                float(dstat1.speaks) if dstat1 else 0,
                float(dstat1.ranks) if dstat1 else 0,
                deb2.name,
                deb2_status,
                float(dstat2.speaks) if dstat2 else 0,
                float(dstat2.ranks) if dstat2 else 0,
            ]

        # Write round data for this team
        for round_stat in round_data:
            if not round_stat:
                writer.writerow([team.get_or_create_team_code(),
                                 team.school.name])
            writer.writerow([
                team.get_or_create_team_code(),
                team.school.name,
                *round_stat,  # Round, Gov/Opp, Win/Loss, Opponent, Judges, Debater 1, N/V, Debater 1 S/R, Debater 2, N/V, Debater 2 S/R, Total
            ])

        # Write the total stats for this team
        writer.writerow([
            team.get_or_create_team_code(),
            team.school.name,
            "Total",
            "",
            "",
            "",
            "",
            "",
            deb1.name,
            deb1_status,
            tot_speaks_deb(deb1),
            tot_ranks_deb(deb1),
            deb2.name,
            deb2_status,
            tot_speaks_deb(deb2),
            tot_ranks_deb(deb2),
            tot_speaks(team),
            tot_ranks(team),
        ])
