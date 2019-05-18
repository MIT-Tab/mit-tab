import random

from mittab.libs import tab_logic, mwmatching, errors
from mittab.apps.tab.models import *


def add_judges(pairings, judges, panel_points):
    # First clear any existing judge assignments
    for pairing in pairings:
        pairing.judges.clear()

    current_round_number = TabSettings.objects.get(key="cur_round").value - 1

    # Try to have consistent ordering with the round display
    random.seed(1337)
    random.shuffle(pairings)
    random.seed(1337)
    random.shuffle(judges)

    # Order the judges and pairings by power ranking
    judges = sorted(judges, key=lambda j: j.rank, reverse=True)
    pairings.sort(key=lambda x: tab_logic.team_comp(x, current_round_number),
                  reverse=True)

    pairing_groups = [list() for panel_point in panel_points] + [list()]
    panel_gaps = {}
    current_group = 0
    for pairing in pairings:
        pairing_groups[current_group].append(pairing)
        if current_group < len(
                panel_points) and pairing == panel_points[current_group][0]:
            panel_gaps[current_group] = panel_points[current_group][1]
            current_group += 1

    for (group_i, group) in enumerate(pairing_groups):
        num_rounds = len(group)
        # Assign chairs (single judges) to each round using perfect pairing
        graph_edges = []
        for (judge_i, judge) in enumerate(judges):
            for (pairing_i, pairing) in enumerate(group):
                if not judge_conflict(judge, pairing.gov_team,
                                      pairing.opp_team):
                    graph_edges.append((pairing_i, judge_i + len(group),
                                        calc_weight(judge_i, pairing_i)))
        judge_assignments = mwmatching.maxWeightMatching(graph_edges,
                                                         maxcardinality=True)
        # If there is no possible assignment of chairs, raise an error
        if -1 in judge_assignments[:num_rounds] or (num_rounds > 0 and not graph_edges):
            if not graph_edges:
                raise errors.JudgeAssignmentError(
                    "Impossible to assign judges, consider reducing your gaps if you"
                    " are making panels, otherwise find some more judges."
                )
            elif -1 in judge_assignments[:num_rounds]:
                pairing_list = judge_assignments[:len(pairings)]
                bad_pairing = pairings[pairing_list.index(-1)]
                raise errors.JudgeAssignmentError(
                    "Could not find a judge for: %s" % str(bad_pairing))
            else:
                raise errors.JudgeAssignmentError()

        # Save the judges to the pairings
        for i in range(num_rounds):
            group[i].judges.add(judges[judge_assignments[i] - num_rounds])
            group[i].chair = judges[judge_assignments[i] - num_rounds]
            group[i].save()

        # Remove any assigned judges from the judging pool
        for pairing in group:
            for judge in pairing.judges.all():
                judges.remove(judge)

        # Function that tries to panel num_to_panel rounds of the potential_pairings
        # Has built in logic to retry with lower number of panels if we fail due
        # to either scratches or wanting to many rounds
        def try_paneling(potential_pairings, all_judges, num_to_panel, gap):
            if not potential_pairings or num_to_panel <= 0:
                # Base case, failed to panel
                print("Failed to panel")
                return {}

            def sort_key(round_obj):
                team_comp_result = tab_logic.team_comp(round_obj, current_round_number)
                get_rank = lambda judge: judge.rank
                rank_tuple = (argmin(round_obj.judges.all(), get_rank).rank,)
                return rank_tuple + tuple([-1 * i for i in team_comp_result])

            rounds = sorted(potential_pairings, key=sort_key)
            base_judge = argmax(
                rounds[:num_to_panel][-1].judges.all(), lambda j: j.rank)
            print("Found maximally ranked judge {0}".format(base_judge))
            potential_panelists = [
                j for j in all_judges
                if j.rank > (float(base_judge.rank) - float(gap))
            ]
            print("Potential panelists:", potential_panelists)
            # If we don't have enough potential panelists, try again with fewer panels
            if len(potential_panelists) < 2 * num_to_panel:
                print("Not enough judges to panel!: ",
                      len(potential_panelists), num_to_panel)
                return try_paneling(potential_pairings, all_judges,
                                    num_to_panel - 1, gap)

            panel_assignments = []
            rounds_to_panel = rounds[:num_to_panel]
            num_to_panel = len(rounds_to_panel)
            for pairing in rounds_to_panel:
                panel_assignments.append([j for j in pairing.judges.all()])

            # Do it twice so we get panels of 3
            for _ in (0, 1):
                graph_edges = []
                for (judge_i, judge) in enumerate(potential_panelists):
                    for (pairing_i, pairing) in enumerate(rounds_to_panel):
                        if not judge_conflict(judge, pairing.gov_team,
                                              pairing.opp_team):
                            judges = panel_assignments[pairing_i] + [judge]
                            graph_edges.append(
                                (pairing_i, judge_i + num_to_panel,
                                 calc_weight_panel(judges)))
                judge_assignments = mwmatching.maxWeightMatching(
                    graph_edges, maxcardinality=True)
                print(judge_assignments)
                if ((-1 in judge_assignments[:num_to_panel])
                        or (num_to_panel > 0 and not graph_edges)):
                    print("Scratches are causing a retry")
                    return try_paneling(potential_pairings, all_judges,
                                        num_to_panel - 1, gap)
                # Save the judges to the potential panel assignments
                judges_used = []
                for i in range(num_to_panel):
                    judge = potential_panelists[judge_assignments[i] -
                                                num_to_panel]
                    panel_assignments[i].append(judge)
                    judges_used.append(judge)
                # Remove any used judges from the potential panelist pool
                for judge in judges_used:
                    print("Removing {0}".format(judge))
                    potential_panelists.remove(judge)

            print("panels: ", panel_assignments)
            result = {}
            for (panel_i, panel) in enumerate(panel_assignments):
                result[rounds_to_panel[panel_i]] = panel
            return result

        # Use the try_paneling function for any rounds that have been marked as panel
        # points, note that we start with trying to panel the entire bracket and
        # rely on try_paneling's retries to fix it
        if group_i in panel_gaps and panel_gaps[group_i]:
            panels = try_paneling(group, judges, len(group),
                                  panel_gaps[group_i])
            for (pairing, panelists) in panels.items():
                for panelist in panelists:
                    if panelist not in pairing.judges.all():
                        pairing.judges.add(panelist)
                        judges.remove(panelist)
                pairing.save()


def argmin(seq, fun):
    return min([(fun(i), i) for i in seq])[1]


def argmax(seq, fun):
    return max([(fun(i), i) for i in seq])[1]


def calc_weight(judge_i, pairing_i):
    """ Calculate the relative badness of this judge assignment

    We want small negative numbers to be preferred to large negative numbers

    """
    if judge_i < pairing_i:
        # if the judge is highly ranked, we can have them judge a lower round
        # if we absolutely have to
        return judge_i - pairing_i
    else:
        return -1 * (judge_i - pairing_i)**2


def calc_weight_panel(judges):
    judge_ranks = [float(j.rank) for j in judges]
    avg = round(float(sum(judge_ranks)) / len(judge_ranks))
    sum_squares = sum([((r - avg)**2) for r in judge_ranks])
    # Use the sum_squares so we get highest panelists with lowest judges
    return 1000000 * sum(judge_ranks) + sum_squares


def judge_conflict(judge, team1, team2):
    return Scratch.objects.filter(judge=judge, team=team1).exists() \
            or had_judge(judge, team1) \
            or Scratch.objects.filter(judge=judge, team=team2).exists() \
            or had_judge(judge, team2)


def had_judge(judge, team):
    return Round.objects.filter(gov_team=team, judges=judge).exists() \
            or Round.objects.filter(opp_team=team, judges=judge).exists()


def can_judge_teams(list_of_judges, team1, team2):
    result = []
    for judge in list_of_judges:
        if not judge_conflict(judge, team1, team2):
            result.append(judge)
    return result
