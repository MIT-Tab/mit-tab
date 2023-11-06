import random

from mittab.libs import tab_logic, mwmatching, errors
from mittab.apps.tab.models import *


def add_judges():
    current_round_number = TabSettings.get("cur_round") - 1

    judges = list(
        Judge.objects.filter(
            checkin__round_number=current_round_number
        ).prefetch_related(
            "judges",  # poorly named relation for the round
            "scratches",
        )
    )
    pairings = tab_logic.sorted_pairings(current_round_number)

    # First clear any existing judge assignments
    Round.judges.through.objects.filter(
        round__round_number=current_round_number
    ).delete()

    # Try to have consistent ordering with the round display
    random.seed(1337)
    random.shuffle(pairings)
    random.seed(1337)
    random.shuffle(judges)

    # Order the judges and pairings by power ranking
    judges = sorted(judges, key=lambda j: j.rank, reverse=True)
    pairings.sort(
        key=lambda x: tab_logic.team_comp(x, current_round_number), reverse=True
    )

    num_rounds = len(pairings)

    # Assign chairs (single judges) to each round using perfect pairing
    graph_edges = []
    for judge_i, judge in enumerate(judges):
        for pairing_i, pairing in enumerate(pairings):
            if not judge_conflict(judge, pairing.gov_team, pairing.opp_team):
                edge = (
                    pairing_i,
                    num_rounds + judge_i,
                    calc_weight(judge_i, pairing_i),
                )
                graph_edges.append(edge)

    judge_assignments = mwmatching.maxWeightMatching(graph_edges, maxcardinality=True)

    # If there is no possible assignment of chairs, raise an error
    if -1 in judge_assignments[:num_rounds] or (num_rounds > 0 and not graph_edges):
        if not graph_edges:
            raise errors.JudgeAssignmentError(
                "Impossible to assign judges, consider reducing your gaps if you"
                " are making panels, otherwise find some more judges."
            )
        elif -1 in judge_assignments[:num_rounds]:
            pairing_list = judge_assignments[: len(pairings)]
            bad_pairing = pairings[pairing_list.index(-1)]
            raise errors.JudgeAssignmentError(
                "Could not find a judge for: %s" % str(bad_pairing)
            )
        else:
            raise errors.JudgeAssignmentError()

    # Because we can't bulk-update the judges field of rounds (it's many-to-many),
    # we use the join table model and bulk-create it
    judge_round_joins = []
    for pairing_i, padded_judge_i in enumerate(judge_assignments[:num_rounds]):
        judge_i = padded_judge_i - num_rounds

        round_obj = pairings[pairing_i]
        judge = judges[judge_i]

        round_obj.chair = judge
        judge_round_joins.append(Round.judges.through(judge=judge, round=round_obj))

    # Save the judges to the pairings
    Round.objects.bulk_update(pairings, ["chair"])
    Round.judges.through.objects.bulk_create(judge_round_joins)


def calc_weight(judge_i, pairing_i):
    """Calculate the relative badness of this judge assignment

    We want small negative numbers to be preferred to large negative numbers

    """
    return -1 * abs(judge_i - (-1 * pairing_i))


def judge_conflict(judge, team1, team2):
    return (
        any(
            s.team_id
            in (
                team1.id,
                team2.id,
            )
            for s in judge.scratches.all()
        )
        or had_judge(judge, team1)
        or had_judge(judge, team2)
    )


def had_judge(judge, team):
    for round_obj in judge.judges.all():
        if round_obj.gov_team_id == team.id or round_obj.opp_team_id == team.id:
            return True
    return False


def can_judge_teams(list_of_judges, team1, team2):
    result = []
    for judge in list_of_judges:
        if not judge_conflict(judge, team1, team2):
            result.append(judge)
    return result
