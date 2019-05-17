from datetime import datetime
from decimal import *
import itertools
import random

from django.db.models import *

from mittab.apps.tab.models import *
from mittab.libs import errors, mwmatching
from mittab.libs.tab_logic.stats import *
from mittab.libs.tab_logic.rankings import *


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
        8) Assign rooms to pairings

    Judges are added later.

    pairings are computed in the following format: [gov,opp,judge,room]
    and then saved immediately into the database
    """
    current_round = TabSettings.get("cur_round")
    validate_round_data(current_round)

    # Set released to false so we don't show pairings
    TabSettings.set("pairing_released", 0)

    # Need a reproduceable random pairing order
    random.seed(0xBEEF)

    # add scratches for teams/judges from the same school
    # NOTE that this only happens if they haven't already been added
    add_scratches_for_school_affil()

    list_of_teams = [None] * current_round
    all_pull_ups = []

    # Record no-shows
    forfeit_teams = list(Team.objects.filter(checked_in=False))
    for team in forfeit_teams:
        lenient_late = TabSettings.get("lenient_late", 0) >= current_round
        no_show = NoShow(no_show_team=team,
                         round_number=current_round,
                         lenient_late=lenient_late)
        no_show.save()

    # If it is the first round, pair by *seed*
    if current_round == 1:
        list_of_teams = list(Team.objects.filter(checked_in=True))

        # If there are an odd number of teams, give a random team the bye
        if len(list_of_teams) % 2 == 1:
            if TabSettings.get("fair_bye", 1) == 0:
                print("Bye: using only unseeded teams")
                possible_teams = [
                    t for t in list_of_teams if t.seed < Team.HALF_SEED
                ]
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
        list_of_teams = sorted(list_of_teams,
                               key=lambda team: team.seed,
                               reverse=True)
    # Otherwise, pair by *speaks*
    else:
        # Bucket all the teams into brackets
        # NOTE: We do not bucket teams that have only won by
        #       forfeit/bye/lenient_late in every round because they have no speaks
        middle_of_bracket = middle_of_bracket_teams()
        all_checked_in_teams = Team.objects.filter(checked_in=True)
        normal_pairing_teams = all_checked_in_teams.exclude(
            pk__in=[t.id for t in middle_of_bracket])

        team_buckets = [(tot_wins(team), team)
                        for team in normal_pairing_teams]
        list_of_teams = [
            rank_teams_except_record(
                [team for (w, team) in team_buckets if w == i])
            for i in range(current_round)
        ]

        for team in middle_of_bracket:
            wins = tot_wins(team)
            print(("Pairing %s into the middle of the %s-win bracket" %
                   (team, wins)))
            bracket_size = len(list_of_teams[wins])
            bracket_middle = bracket_size // 2
            list_of_teams[wins].insert(bracket_middle, team)

        # Correct for brackets with odd numbers of teams
        #  1) If we are in the bottom bracket, give someone a bye
        #  2) If we are in 1-up bracket and there are no all down
        #     teams, give someone a bye
        #  3) Otherwise, find a pull up from the next bracket
        for bracket in reversed(list(range(current_round))):
            if len(list_of_teams[bracket]) % 2 != 0:
                # If there are no teams all down, give the bye to a one down team.
                if bracket == 0:
                    byeint = len(list_of_teams[bracket]) - 1
                    bye = Bye(bye_team=list_of_teams[bracket][byeint],
                              round_number=current_round)
                    bye.save()
                    list_of_teams[bracket].remove(
                        list_of_teams[bracket][byeint])
                elif bracket == 1 and not list_of_teams[0]:
                    # in 1 up and no all down teams
                    found_bye = False
                    for byeint in range(len(list_of_teams[1]) - 1, -1, -1):
                        if had_bye(list_of_teams[1][byeint]):
                            pass
                        elif not found_bye:
                            bye = Bye(bye_team=list_of_teams[1][byeint],
                                      round_number=current_round)
                            bye.save()
                            list_of_teams[1].remove(list_of_teams[1][byeint])
                            found_bye = True
                    if not found_bye:
                        raise errors.NotEnoughTeamsError()
                else:
                    pull_up = None
                    # i is the last team in the bracket below
                    i = len(list_of_teams[bracket - 1]) - 1
                    pullup_rounds = Round.objects.exclude(pullup=Round.NONE)
                    teams_been_pulled_up = [
                        r.gov_team for r in pullup_rounds
                        if r.pullup == Round.GOV
                    ]
                    teams_been_pulled_up.extend([
                        r.opp_team for r in pullup_rounds
                        if r.pullup == Round.OPP
                    ])

                    # try to pull-up the lowest-ranked team that hasn't been
                    # pulled-up. Fall-back to the lowest-ranked team if all have
                    # been pulled-up
                    not_pulled_up_teams = [
                        t for t in list_of_teams[bracket - 1]
                        if t not in teams_been_pulled_up
                    ]
                    if not_pulled_up_teams:
                        pull_up = not_pulled_up_teams[-1]
                    else:
                        pull_up = list_of_teams[bracket - 1][-1]

                    all_pull_ups.append(pull_up)
                    list_of_teams[bracket].append(pull_up)
                    list_of_teams[bracket - 1].remove(pull_up)

                    # after adding pull-up to new bracket and deleting from old,
                    # sort again by speaks making sure to leave any first
                    # round bye in the correct spot
                    removed_teams = []
                    for team in list(Team.objects.filter(checked_in=True)):
                        # They have all wins and they haven't forfeited so
                        # they need to get paired in
                        if team in middle_of_bracket and tot_wins(
                                team) == bracket:
                            removed_teams += [team]
                            list_of_teams[bracket].remove(team)
                    list_of_teams[bracket] = rank_teams_except_record(
                        list_of_teams[bracket])
                    for team in removed_teams:
                        list_of_teams[bracket].insert(
                            len(list_of_teams[bracket]) // 2, team)

    # Pass in the prepared nodes to the perfect pairing logic
    # to get a pairing for the round
    pairings = []
    for bracket in range(current_round):
        if current_round == 1:
            temp = perfect_pairing(list_of_teams)
        else:
            temp = perfect_pairing(list_of_teams[bracket])
            print("Pairing round %i of size %i" % (bracket, len(temp)))
        for pair in temp:
            pairings.append([pair[0], pair[1], None])

    if current_round == 1:
        random.shuffle(pairings, random=random.random)
        pairings = sorted(pairings,
                          key=lambda team: highest_seed(team[0], team[1]),
                          reverse=True)
    # sort with pairing with highest ranked team first
    else:
        sorted_teams = [s.team for s in rank_teams()]
        pairings = sorted(pairings,
                          key=lambda team: min(sorted_teams.index(team[0]),
                                               sorted_teams.index(team[1])))

    # Assign rooms (does this need to be random? maybe bad to have top
    #               ranked teams/judges in top rooms?)
    rooms = Room.objects.all()
    rooms = sorted(rooms, key=lambda r: r.rank, reverse=True)

    for i, pairing in enumerate(pairings):
        pairing[2] = rooms[i]

    # Enter into database
    for gov, opp, room in pairings:
        round_obj = Round(round_number=current_round,
                          gov_team=gov,
                          opp_team=opp,
                          room=room)
        if gov in all_pull_ups:
            round_obj.pullup = Round.GOV
        elif opp in all_pull_ups:
            round_obj.pullup = Round.OPP
        round_obj.save()


def have_enough_judges(round_to_check):
    future_rounds = Team.objects.filter(checked_in=True).count() // 2
    num_judges = CheckIn.objects.filter(round_number=round_to_check).count()
    if num_judges < future_rounds:
        return False, (num_judges, future_rounds)
    return True, (num_judges, future_rounds)


def have_enough_rooms(_round_to_check):
    future_rounds = Team.objects.filter(checked_in=True).count() // 2
    num_rooms = Room.objects.filter(rank__gt=0).count()
    if num_rooms < future_rounds:
        return False, (num_rooms, future_rounds)
    return True, (num_rooms, future_rounds)


def have_properly_entered_data(round_to_check):
    last_round = round_to_check - 1
    prev_rounds = Round.objects.filter(round_number=last_round)
    for prev_round in prev_rounds:
        # There should be a result
        if prev_round.victor == Round.NONE:
            raise errors.PrevRoundNotEnteredError()
        # Both teams should not have byes or noshows
        gov_team, opp_team = prev_round.gov_team, prev_round.opp_team
        for team in gov_team, opp_team:
            had_noshow = NoShow.objects.filter(no_show_team=team,
                                               round_number=last_round)
            if had_bye(team, last_round):
                raise errors.ByeAssignmentError(
                    "{} both had a bye and debated last round".format(team))
            if had_noshow:
                raise errors.NoShowAssignmentError(
                    "{} both debated and had a no show".format(team))


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


def add_scratches_for_school_affil():
    """
    Add scratches for teams/judges from the same school
    Only do this if they haven't already been added
    """
    all_judges = Judge.objects.all()
    all_teams = Team.objects.all()
    for judge in all_judges:
        for team in all_teams:
            judge_schools = judge.schools.all()
            if team.school in judge_schools or team.hybrid_school in judge_schools:
                if not Scratch.objects.filter(judge=judge, team=team).exists():
                    Scratch.objects.create(judge=judge,
                                           team=team,
                                           scratch_type=1)


def highest_seed(team1, team2):
    return max(team1.seed, team2.seed)


# Check if two teams have hit before
def hit_before(team1, team2):
    return Round.objects.filter(gov_team=team1, opp_team=team2).exists() or \
            Round.objects.filter(gov_team=team2, opp_team=team1).exists()


def middle_of_bracket_teams():
    """
    Finds teams whose only rounds have been one of the following results:

    1 - win by forfeit
    2 - lenient_late rounds
    3 - byes

    These teams have speaks of zero but _should_ have average speaks, so they
    should be paired into the middle of their bracket. Randomized for fairness
    """
    teams = []
    for team in Team.objects.filter(checked_in=True):
        avg_speaks_rounds = Bye.objects.filter(bye_team=team).count()
        avg_speaks_rounds += NoShow.objects.filter(no_show_team=team,
                                                   lenient_late=True).count()
        avg_speaks_rounds += num_forfeit_wins(team)
        if TabSettings.get("cur_round") - 1 == avg_speaks_rounds:
            teams.append(team)
    random.shuffle(teams)
    return teams


def team_comp(pairing, round_number):
    gov, opp = pairing.gov_team, pairing.opp_team
    if round_number == 1:
        return (max(gov.seed, opp.seed), min(gov.seed, opp.seed))
    else:
        return (max(tot_wins(gov),
                    tot_wins(opp)), max(tot_speaks(gov), tot_speaks(opp)),
                min(tot_speaks(gov), tot_speaks(opp)))


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
        TEAM_CHECKED_IN, TEAM_NOT_CHECKED_IN, JUDGE_CHECKED_IN_CUR,
        JUDGE_NOT_CHECKED_IN_CUR, LOW_RANKED_JUDGE, MID_RANKED_JUDGE,
        HIGH_RANKED_JUDGE, ROOM_ZERO_RANK, ROOM_NON_ZERO_RANK,
        JUDGE_CHECKED_IN_NEXT, JUDGE_NOT_CHECKED_IN_NEXT
    ]

    @staticmethod
    def translate_flag(flag, short=False):
        return {
            TabFlags.TEAM_NOT_CHECKED_IN: ("Team NOT Checked In", "*"),
            TabFlags.TEAM_CHECKED_IN: ("Team Checked In", ""),
            TabFlags.JUDGE_CHECKED_IN_CUR:
            ("Judge Checked In, Current Round", ""),
            TabFlags.JUDGE_NOT_CHECKED_IN_CUR:
            ("Judge NOT Checked In, Current Round", "*"),
            TabFlags.JUDGE_CHECKED_IN_NEXT:
            ("Judge Checked In, Next Round", ""),
            TabFlags.JUDGE_NOT_CHECKED_IN_NEXT:
            ("Judge NOT Checked In, Next Round", "!"),
            TabFlags.LOW_RANKED_JUDGE: ("Low Ranked Judge", "L"),
            TabFlags.MID_RANKED_JUDGE: ("Mid Ranked Judge", "M"),
            TabFlags.HIGH_RANKED_JUDGE: ("High Ranked Judge", "H"),
            TabFlags.ROOM_ZERO_RANK: ("Room has rank of 0", "*"),
            TabFlags.ROOM_NON_ZERO_RANK: ("Room has rank > 0", "")
        }.get(flag, ("Flag Not Found", "U"))[short]

    @staticmethod
    def flags_to_symbols(flags):
        return "".join([
            TabFlags.translate_flag(flag, True) for flag in TabFlags.ALL_FLAGS
            if flags & flag == flag
        ])

    @staticmethod
    def get_filters_and_symbols(all_flags):
        flat_flags = list(itertools.chain(*all_flags))
        filters = [[(flag, TabFlags.translate_flag(flag))
                    for flag in flag_group] for flag_group in all_flags]
        symbol_text = [(TabFlags.translate_flag(flag, True),
                        TabFlags.translate_flag(flag)) for flag in flat_flags
                       if TabFlags.translate_flag(flag, True)]
        return filters, symbol_text


def perfect_pairing(list_of_teams):
    """ Uses the mwmatching library to assign teams in a pairing """
    graph_edges = []
    for i, team1 in enumerate(list_of_teams):
        for j, team2 in enumerate(list_of_teams):
            if i > j:
                weight = calc_weight(team1, team2, i, j,
                                     list_of_teams[len(list_of_teams) - i - 1],
                                     list_of_teams[len(list_of_teams) - j - 1],
                                     len(list_of_teams) - i - 1,
                                     len(list_of_teams) - j - 1)
                graph_edges += [(i, j, weight)]
    pairings_num = mwmatching.maxWeightMatching(graph_edges,
                                                maxcardinality=True)
    all_pairs = []
    for pair in pairings_num:
        if pair < len(list_of_teams):
            team = list_of_teams[pair]
            matched_team = list_of_teams[pairings_num.index(pair)]
            pairing = set([team, matched_team])

            if pairing not in all_pairs:
                all_pairs.append(pairing)
    return determine_gov_opp(all_pairs)


def calc_weight(team_a, team_b, team_a_ind, team_b_ind, team_a_opt, team_b_opt,
                team_a_opt_ind, team_b_opt_ind):
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

    current_round = TabSettings.get("cur_round", 1)
    tot_rounds = TabSettings.get("tot_rounds", 5)
    power_pairing_multiple = TabSettings.get("power_pairing_multiple", -1)
    high_opp_penalty = TabSettings.get("high_opp_penalty", 0)
    high_gov_penalty = TabSettings.get("high_gov_penalty", -100)
    high_high_opp_penalty = TabSettings.get("higher_opp_penalty", -10)
    same_school_penalty = TabSettings.get("same_school_penalty", -1000)
    hit_pull_up_before = TabSettings.get("hit_pull_up_before", -10000)
    hit_team_before = TabSettings.get("hit_team_before", -100000)

    if current_round == 1:
        weight = power_pairing_multiple * (
            abs(team_a_opt.seed - team_b.seed) +
            abs(team_b_opt.seed - team_a.seed)) / 2.0
    else:
        weight = power_pairing_multiple * (
            abs(team_a_opt_ind - team_b_ind) +
            abs(team_b_opt_ind - team_a_ind)) / 2.0

    half = int(tot_rounds // 2) + 1
    if num_opps(team_a) >= half and num_opps(team_b) >= half:
        weight += high_opp_penalty

    if num_opps(team_a) >= half + 1 and num_opps(team_b) >= half + 1:
        weight += high_high_opp_penalty

    if num_govs(team_a) >= half and num_govs(team_b) >= half:
        weight += high_gov_penalty

    if team_a.school == team_b.school:
        weight += same_school_penalty

    if (hit_pull_up(team_a) and tot_wins(team_b) < tot_wins(team_a)) or (
            hit_pull_up(team_b) and tot_wins(team_a) < tot_wins(team_b)):
        weight += hit_pull_up_before

    if hit_before(team_a, team_b):
        weight += hit_team_before

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
