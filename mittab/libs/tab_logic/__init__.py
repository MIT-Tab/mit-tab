from datetime import datetime
from decimal import *
import itertools
import random

from django.db.models import *

from mittab.apps.tab.models import *
from mittab.libs import errors, mwmatching
from mittab.libs.tab_logic.stats import *
from mittab.libs.tab_logic.rankings import *


def _re_rank_bracket_after_pull_up(
        brackets, bracket, all_checked_in_teams, middle_of_bracket):
    removed_teams = []
    for team in all_checked_in_teams:
        if (
                team in middle_of_bracket
                and tot_wins(team) == bracket
                and team in brackets[bracket]
        ):
            removed_teams.append(team)
            brackets[bracket].remove(team)

    brackets[bracket] = rank_teams_except_record(brackets[bracket])
    for team in removed_teams:
        brackets[bracket].insert(len(brackets[bracket]) // 2, team)


def _ordered_pullup_candidates(source_bracket, historical_pullups):
    not_pulled_up = [team for team in source_bracket if team not in historical_pullups]
    already_pulled_up = [team for team in source_bracket if team in historical_pullups]
    return list(reversed(not_pulled_up)) + list(reversed(already_pulled_up))


def _build_pairings_for_brackets(brackets, current_round):
    pairings = []
    for bracket in range(current_round):
        temp = perfect_pairing(brackets[bracket])
        if current_round != 1:
            print(f"Pairing bracket {bracket} of size {len(temp)}")
        for pair in temp:
            pairings.append([pair[0], pair[1]])
    return pairings


def _pair_brackets_with_pullup_search(
        brackets,
        current_round,
        all_checked_in_teams,
        middle_of_bracket,
        historical_pullups):
    def search(working_brackets, bracket_index, selected_pullups, bye_team):
        while bracket_index >= 0 and len(working_brackets[bracket_index]) % 2 == 0:
            bracket_index -= 1

        if bracket_index < 0:
            try:
                pairings = _build_pairings_for_brackets(working_brackets, current_round)
            except errors.NotEnoughTeamsError:
                return None
            return pairings, selected_pullups, bye_team

        if bracket_index == 0:
            candidate_byes = working_brackets[0][-1:]
            for candidate_bye in candidate_byes:
                next_brackets = [list(bracket) for bracket in working_brackets]
                next_brackets[0].remove(candidate_bye)
                result = search(
                    next_brackets,
                    bracket_index - 1,
                    list(selected_pullups),
                    candidate_bye,
                )
                if result is not None:
                    return result
            return None

        if bracket_index == 1 and not working_brackets[0]:
            candidate_byes = [
                team for team in reversed(working_brackets[1]) if not had_bye(team)
            ]
            for candidate_bye in candidate_byes:
                next_brackets = [list(bracket) for bracket in working_brackets]
                next_brackets[1].remove(candidate_bye)
                result = search(
                    next_brackets,
                    bracket_index - 1,
                    list(selected_pullups),
                    candidate_bye,
                )
                if result is not None:
                    return result
            return None

        for pull_up in _ordered_pullup_candidates(
                working_brackets[bracket_index - 1],
                historical_pullups):
            next_brackets = [list(bracket) for bracket in working_brackets]
            next_selected_pullups = list(selected_pullups)
            next_selected_pullups.append(pull_up)
            next_brackets[bracket_index].append(pull_up)
            next_brackets[bracket_index - 1].remove(pull_up)
            _re_rank_bracket_after_pull_up(
                next_brackets,
                bracket_index,
                all_checked_in_teams,
                middle_of_bracket,
            )
            result = search(
                next_brackets,
                bracket_index - 1,
                next_selected_pullups,
                bye_team,
            )
            if result is not None:
                return result
        return None

    result = search(
        [list(bracket) for bracket in brackets],
        current_round - 1,
        [],
        None,
    )
    if result is None:
        raise errors.NotEnoughTeamsError(
            "Could not satisfy all scratch constraints while pairing teams."
        )
    return result


def pair_round():
    """
    Pair the next round of debate.
    This function will do the following:
        1) Verify we can pair the next round
        2) Check that we have scratched all judges from
           teams of the same school, and if not add these
           scratches
        3) Record no-show teams
        4) Setup the list of teams by either seed or speaks
        5) Calculate byes
        6) Calculate pull ups based on byes
        7) Pass in evened brackets to the perfect pairing algorithm

    Judges are added later.

    pairings are computed in the following format: [gov,opp,judge]
    and then saved immediately into the database
    """
    current_round = TabSettings.get("cur_round")
    validate_round_data(current_round)

    # Set released to false so we don't show pairings
    TabSettings.set("pairing_released", 0)

    # Need a reproduceable random pairing order
    random.seed(0xBEEF)

    all_pull_ups = []

    # Record no-shows
    forfeit_teams = list(Team.objects.filter(checked_in=False))
    for team in forfeit_teams:
        no_show = NoShow(
            no_show_team=team, round_number=current_round
        )
        no_show.save()

    # If it is the first round, pair by *seed*
    all_checked_in_teams = Team.with_preloaded_relations_for_tabbing() \
            .filter(checked_in=True)

    if current_round == 1:
        list_of_teams = list(all_checked_in_teams)

        # If there are an odd number of teams, give a random team the bye
        if len(list_of_teams) % 2 == 1:
            if TabSettings.get("fair_bye", 1) == 0:
                print("Bye: using only unseeded teams")
                possible_teams = [t for t in list_of_teams if t.seed < Team.HALF_SEED]
            else:
                print("Bye: using all teams")
                possible_teams = list_of_teams
            bye_team = random.choice(possible_teams)
            bye = Bye(bye_team=bye_team, round_number=current_round)
            bye.save()
            list_of_teams.remove(bye.bye_team)

        # Sort the teams by seed. We must randomize beforehand so that similarly
        # seeded teams are paired randomly.
        random.shuffle(list_of_teams)
        list_of_teams = sorted(list_of_teams, key=lambda team: team.seed, reverse=True)
    # Otherwise, pair by *speaks*
    else:
        # Bucket all the teams into brackets
        # NOTE: We do not bucket teams that have only won by
        #       forfeit/bye/lenient_late in every round because they have no speaks
        middle_of_bracket, normal_pairing_teams = get_middle_and_non_middle_teams(
            all_checked_in_teams
        )

        team_buckets = [(tot_wins(team), team) for team in normal_pairing_teams]
        list_of_teams = [
            rank_teams_except_record([team for (w, team) in team_buckets if w == i])
            for i in range(current_round)
        ]

        for team in middle_of_bracket:
            wins = tot_wins(team)
            print(f"Pairing {team} into the middle of the {wins}-win bracket")
            bracket_size = len(list_of_teams[wins])
            bracket_middle = bracket_size // 2
            list_of_teams[wins].insert(bracket_middle, team)

        pullup_rounds = Round.objects.exclude(pullup=Round.NONE)
        teams_been_pulled_up = [
            r.gov_team for r in pullup_rounds if r.pullup == Round.GOV
        ]
        teams_been_pulled_up.extend(
            [r.opp_team for r in pullup_rounds if r.pullup == Round.OPP]
        )

        pairings, all_pull_ups, bye_team = _pair_brackets_with_pullup_search(
            list_of_teams,
            current_round,
            all_checked_in_teams,
            middle_of_bracket,
            teams_been_pulled_up,
        )
        if bye_team is not None:
            Bye.objects.create(bye_team=bye_team, round_number=current_round)

    if current_round == 1:
        pairings = []
        for pair in perfect_pairing(list_of_teams):
            pairings.append([pair[0], pair[1]])

    if current_round == 1:
        random.shuffle(pairings)
        pairings = sorted(
            pairings, key=lambda team: highest_seed(team[0], team[1]), reverse=True
        )
    # sort with pairing with highest ranked team first
    else:
        sorted_teams = [s.team for s in rank_teams()]
        pairings = sorted(
            pairings,
            key=lambda team: min(
                sorted_teams.index(team[0]), sorted_teams.index(team[1])
            ),
        )

    # Enter into database
    all_rounds = []
    for gov, opp in pairings:
        round_obj = Round(
            round_number=current_round, gov_team=gov, opp_team=opp
        )
        if gov in all_pull_ups:
            round_obj.pullup = Round.GOV
        elif opp in all_pull_ups:
            round_obj.pullup = Round.OPP
        all_rounds.append(round_obj)
    Round.objects.bulk_create(all_rounds)


def have_enough_judges(round_to_check):
    future_rounds = Team.objects.filter(checked_in=True).count() // 2
    num_judges = CheckIn.objects.filter(round_number=round_to_check).count()
    if num_judges < future_rounds:
        return False, (num_judges, future_rounds)
    return True, (num_judges, future_rounds)


def have_enough_rooms(_round_to_check):
    future_rounds = Team.objects.filter(checked_in=True).count() // 2
    num_rooms = RoomCheckIn.objects.filter(round_number=_round_to_check).count()
    if num_rooms < future_rounds:
        return False, (num_rooms, future_rounds)
    return True, (num_rooms, future_rounds)


def have_properly_entered_data(round_to_check):
    last_round = round_to_check - 1
    prev_rounds = Round.objects.filter(round_number=last_round).prefetch_related(
        "gov_team", "opp_team"
    )
    prev_round_noshows = set(
        NoShow.objects.filter(round_number=last_round).values_list(
            "no_show_team_id", flat=True
        )
    )
    prev_round_byes = set(
        Bye.objects.filter(round_number=last_round).values_list("bye_team", flat=True)
    )

    for prev_round in prev_rounds:
        # There should be a result
        if prev_round.victor == Round.NONE:
            raise errors.PrevRoundNotEnteredError()
        # Both teams should not have byes or noshows
        gov_team, opp_team = prev_round.gov_team, prev_round.opp_team
        for team in gov_team, opp_team:
            if team.id in prev_round_byes:
                raise errors.ByeAssignmentError(
                    f"{team} both had a bye and debated last round"
                )
            if team.id in prev_round_noshows:
                raise errors.NoShowAssignmentError(
                    f"{team} both debated and had a no show"
                )


def validate_round_data(round_to_check):
    """
    Validate that the current round has all the data we expect before
    pairing the next round
    In particular we require:
        1) N/2 judges that are *checked in*
        2) N/2 rooms available
        3) All rounds must be entered from the previous round
        4) Check that no teams both have a round result and a NoShow or Bye

    If any of these fail we raise a specific error as to the type of error
    """

    # Check that there are enough judges
    if not have_enough_judges(round_to_check)[0]:
        raise errors.NotEnoughJudgesError()

    # Check there are enough rooms
    if not have_enough_rooms(round_to_check)[0]:
        raise errors.NotEnoughRoomsError()

    # If we have results, they should be entered and there should be no
    # byes or noshows for teams that debated
    have_properly_entered_data(round_to_check)

def highest_seed(team1, team2):
    return max(team1.seed, team2.seed)


# Check if two teams have hit before
def hit_before(team1, team2):
    for round_obj in team1.gov_team.all():
        if round_obj.opp_team == team2:
            return True
    for round_obj in team1.opp_team.all():
        if round_obj.gov_team == team2:
            return True
    return False


def get_middle_and_non_middle_teams(all_teams):
    """
    Given a list of teams, splits the list into two. The first value will be
    a list of teams who should be in the middle of the bracket because all of their
    rounds have been one of the following:

    1 - win by forfeit
    2 - lenient_late rounds
    3 - byes

    These teams have speaks of zero but _should_ have average speaks, so they
    should be paired into the middle of their bracket. Randomized for fairness

    The second list will be all of the teams not in the original list
    """
    middle_of_bracket, non_middle_of_bracket = [], []
    all_teams = list(all_teams)
    random.shuffle(all_teams)
    round_count = TabSettings.get("cur_round") - 1

    for team in all_teams:
        avg_speaks_rounds = team.byes.count()
        for no_show in team.no_shows.all():
            if no_show.lenient_late:
                avg_speaks_rounds += 1
        avg_speaks_rounds += num_forfeit_wins(team)

        if round_count == avg_speaks_rounds:
            middle_of_bracket.append(team)
        else:
            non_middle_of_bracket.append(team)

    return middle_of_bracket, non_middle_of_bracket


def sorted_pairings(round_number, outround=False):
    """
    Helper function to get the sorted pairings for a round while minimizing the
    number of DB queries required to calculate it
    """
    fields = [
        "judges",
        "judges__judges",
        "chair",
        "room",
        "gov_team",
        "opp_team",
        "gov_team__breaking_team",
        "gov_team__gov_team",  # poorly named relation, points to rounds as gov
        "gov_team__opp_team",  # poorly named relation, points to rounds as gov
        "gov_team__byes",
        "gov_team__no_shows",
        "gov_team__debaters__team_set",
        "gov_team__debaters__team_set__byes",
        "gov_team__debaters__team_set__no_shows",
        "gov_team__debaters__roundstats_set",
        "gov_team__debaters__roundstats_set__round",
        "gov_team__gov_team_outround",
        "gov_team__opp_team_outround",
        "opp_team__breaking_team",
        "opp_team__gov_team",  # poorly named relation, points to rounds as gov
        "opp_team__opp_team",  # poorly named relation, points to rounds as gov
        "opp_team__byes",
        "opp_team__no_shows",
        "opp_team__debaters__team_set",
        "opp_team__debaters__team_set__byes",
        "opp_team__debaters__team_set__no_shows",
        "opp_team__debaters__roundstats_set",
        "opp_team__debaters__roundstats_set__round",
        "opp_team__gov_team_outround",
        "opp_team__opp_team_outround",
        "room__tags", "judges__required_room_tags",
        "gov_team__required_room_tags",
        "opp_team__required_room_tags"
    ]
    model = Outround if outround else Round
    filter_field = "num_teams" if outround else "round_number"

    round_pairing = list(
        model.objects.filter(**{filter_field: round_number}).prefetch_related(*fields)
    )
    round_pairing.sort(key=lambda x: team_comp(x, round_number), reverse=True)

    return round_pairing


def team_comp(pairing, round_number):
    gov, opp = pairing.gov_team, pairing.opp_team
    if round_number == 1:
        return (max(gov.seed, opp.seed), min(gov.seed, opp.seed))
    else:
        return (
            max(tot_wins(gov), tot_wins(opp)),
            max(tot_speaks(gov), tot_speaks(opp)),
            min(tot_speaks(gov), tot_speaks(opp)),
        )


def team_score_except_record(team):
    team_score = TeamScore(team)
    team_score.wins = 0
    return team_score.scoring_tuple()


def rank_teams_except_record(teams):
    return sorted(teams, key=team_score_except_record)


class TabFlags:
    TEAM_CHECKED_IN = 1 << 0
    TEAM_NOT_CHECKED_IN = 1 << 1
    JUDGE_CHECKED_IN_CUR = 1 << 2
    JUDGE_NOT_CHECKED_IN_CUR = 1 << 3
    LOW_RANKED_JUDGE = 1 << 4
    MID_RANKED_JUDGE = 1 << 5
    HIGH_RANKED_JUDGE = 1 << 6
    ROOM_ZERO_RANK = 1 << 7
    ROOM_NON_ZERO_RANK = 1 << 8
    JUDGE_CHECKED_IN_NEXT = 1 << 9
    JUDGE_NOT_CHECKED_IN_NEXT = 1 << 10

    ALL_FLAGS = [
        TEAM_CHECKED_IN,
        TEAM_NOT_CHECKED_IN,
        JUDGE_CHECKED_IN_CUR,
        JUDGE_NOT_CHECKED_IN_CUR,
        LOW_RANKED_JUDGE,
        MID_RANKED_JUDGE,
        HIGH_RANKED_JUDGE,
        ROOM_ZERO_RANK,
        ROOM_NON_ZERO_RANK,
        JUDGE_CHECKED_IN_NEXT,
        JUDGE_NOT_CHECKED_IN_NEXT,
    ]

    @staticmethod
    def translate_flag(flag, short=False):
        return {
            TabFlags.TEAM_NOT_CHECKED_IN: ("Team NOT Checked In", "*"),
            TabFlags.TEAM_CHECKED_IN: ("Team Checked In", ""),
            TabFlags.JUDGE_CHECKED_IN_CUR: ("Judge Checked In, Current Round", ""),
            TabFlags.JUDGE_NOT_CHECKED_IN_CUR: (
                "Judge NOT Checked In, Current Round",
                "*",
            ),
            TabFlags.JUDGE_CHECKED_IN_NEXT: ("Judge Checked In, Next Round", ""),
            TabFlags.JUDGE_NOT_CHECKED_IN_NEXT: (
                "Judge NOT Checked In, Next Round",
                "!",
            ),
            TabFlags.LOW_RANKED_JUDGE: ("Low Ranked Judge", "L"),
            TabFlags.MID_RANKED_JUDGE: ("Mid Ranked Judge", "M"),
            TabFlags.HIGH_RANKED_JUDGE: ("High Ranked Judge", "H"),
            TabFlags.ROOM_ZERO_RANK: ("Room has rank of 0", "*"),
            TabFlags.ROOM_NON_ZERO_RANK: ("Room has rank > 0", ""),
        }.get(flag, ("Flag Not Found", "U"))[short]

    @staticmethod
    def flags_to_symbols(flags):
        return "".join(
            [
                TabFlags.translate_flag(flag, True)
                for flag in TabFlags.ALL_FLAGS
                if flags & flag == flag
            ]
        )

    @staticmethod
    def get_filters_and_symbols(all_flags):
        flat_flags = list(itertools.chain(*all_flags))
        filters = [
            [(flag, TabFlags.translate_flag(flag)) for flag in flag_group]
            for flag_group in all_flags
        ]
        symbol_text = [
            (TabFlags.translate_flag(flag, True), TabFlags.translate_flag(flag))
            for flag in flat_flags
            if TabFlags.translate_flag(flag, True)
        ]
        return filters, symbol_text


def perfect_pairing(list_of_teams):
    """Uses the mwmatching library to assign teams in a pairing"""
    graph_edges = []
    weights = get_weights()
    scratched_pairs = {
        (team_one, team_two)
        for team_one, team_two in TeamTeamScratch.objects.values_list(
            "team_one_id", "team_two_id"
        )
    }
    for i, team1 in enumerate(list_of_teams):
        for j, team2 in enumerate(list_of_teams):
            if i > j:
                if team1.id and team2.id:
                    pair_key = (min(team1.id, team2.id), max(team1.id, team2.id))
                    if pair_key in scratched_pairs:
                        continue
                weight = calc_weight(
                    team1,
                    team2,
                    i,
                    j,
                    list_of_teams[len(list_of_teams) - i - 1],
                    list_of_teams[len(list_of_teams) - j - 1],
                    len(list_of_teams) - i - 1,
                    len(list_of_teams) - j - 1,
                    weights,
                    TabSettings.get("cur_round", 1),
                    TabSettings.get("tot_rounds", 5),
                )
                graph_edges += [(i, j, weight)]
    pairings_num = mwmatching.maxWeightMatching(graph_edges, maxcardinality=True)
    if (
            len(pairings_num) < len(list_of_teams)
            or -1 in pairings_num[:len(list_of_teams)]
    ):
        raise errors.NotEnoughTeamsError(
            "Could not satisfy all scratch constraints while pairing teams."
        )
    all_pairs = []
    for pair in pairings_num:
        if pair < len(list_of_teams):
            team = list_of_teams[pair]
            matched_team = list_of_teams[pairings_num.index(pair)]
            pairing = set([team, matched_team])

            if pairing not in all_pairs:
                all_pairs.append(pairing)
    return determine_gov_opp(all_pairs)


def get_weights():
    """
    Returns a map of all the weight-related tab settings to use without querying for
    calculations
    """
    return {
        "power_pairing_multiple": TabSettings.get("power_pairing_multiple", -1),
        "high_opp_penalty": TabSettings.get("high_opp_penalty", 0),
        "high_gov_penalty": TabSettings.get("high_gov_penalty", -100),
        "high_high_opp_penalty": TabSettings.get("higher_opp_penalty", -10),
        "same_school_penalty": TabSettings.get("same_school_penalty", -1000),
        "hit_pull_up_before": TabSettings.get("hit_pull_up_before", -10000),
        "hit_team_before": TabSettings.get("hit_team_before", -100000),
    }


def calc_weight(
        team_a,
        team_b,
        team_a_ind,
        team_b_ind,
        team_a_opt,
        team_b_opt,
        team_a_opt_ind,
        team_b_opt_ind,
        weights,
        current_round,
        tot_rounds,
):
    """
    Calculate the penalty for a given pairing

    Args:
        team_a - the first team in the pairing
        team_b - the second team in the pairing
        team_a_ind - the position in the pairing of team_a
        team_b_ind - the position in the pairing of team_b
        team_a_opt - the optimal power paired team for team_a to be paired with
        team_b_opt - the optimal power paired team for team_b to be paired with
        team_a_opt_ind - the position in the pairing of team_a_opt
        team_b_opt_ind - the position in the pairing of team_b_opt
    """
    if current_round == 1:
        weight = (
            weights["power_pairing_multiple"]
            * (abs(team_a_opt.seed - team_b.seed) + abs(team_b_opt.seed - team_a.seed))
            / 2.0
        )
    else:
        weight = (
            weights["power_pairing_multiple"]
            * (abs(team_a_opt_ind - team_b_ind) + abs(team_b_opt_ind - team_a_ind))
            / 2.0
        )

    half = int(tot_rounds // 2) + 1
    if num_opps(team_a) >= half and num_opps(team_b) >= half:
        weight += weights["high_opp_penalty"]

    if num_opps(team_a) >= half + 1 and num_opps(team_b) >= half + 1:
        weight += weights["high_high_opp_penalty"]

    if num_govs(team_a) >= half and num_govs(team_b) >= half:
        weight += weights["high_gov_penalty"]

    if team_a.school_id == team_b.school_id:
        weight += weights["same_school_penalty"]

    if (hit_pull_up(team_a) and tot_wins(team_b) < tot_wins(team_a)) or (
            hit_pull_up(team_b) and tot_wins(team_a) < tot_wins(team_b)
    ):
        weight += weights["hit_pull_up_before"]

    if hit_before(team_a, team_b):
        weight += weights["hit_team_before"]

    return weight


def determine_gov_opp(all_pairs):
    final_pairings = []
    for team1, team2 in all_pairs:
        if num_govs(team1) < num_govs(team2):
            # team1 should be gov
            final_pairings += [[team1, team2]]
        elif num_govs(team2) < num_govs(team1):
            # team2 should be gov
            final_pairings += [[team2, team1]]
        elif num_opps(team1) < num_opps(team2):
            # team2 should be gov
            final_pairings += [[team2, team1]]
        elif num_opps(team2) < num_opps(team1):
            # team1 should be gov
            final_pairings += [[team1, team2]]
        elif random.randint(0, 1) == 0:
            final_pairings += [[team1, team2]]
        else:
            final_pairings += [[team2, team1]]
    return final_pairings


def clear_current_round_pairing():
    """
    Safely clear all pairing data for the current round.
    This removes Rounds, Byes, and NoShows for the current round number.

    This is used when re-pairing a round to ensure a clean slate.
    The current round number is NOT decremented - it stays the same.

    Returns the round number that was cleared.
    """
    current_round = TabSettings.get("cur_round") - 1

    if current_round < 1:
        raise ValueError("No round has been paired yet")

    Round.objects.filter(round_number=current_round).delete()
    Bye.objects.filter(round_number=current_round).delete()
    NoShow.objects.filter(round_number=current_round).delete()
    TabSettings.set("pairing_released", 0)

    return current_round
