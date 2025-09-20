import random
from django.db.models import Min
from mittab.libs import tab_logic, mwmatching, errors
from mittab.apps.tab.models import *


def add_judges():
    current_round_number = TabSettings.get("cur_round") - 1

    # First clear any existing judge assignments
    Round.judges.through.objects.filter(
        round__round_number=current_round_number
    ).delete()

    judges = list(
        Judge.objects.filter(
            checkin__round_number=current_round_number
        ).prefetch_related(
            "judges",  # poorly named relation for the round
            "scratches",
        )
    )
    pairings = tab_logic.sorted_pairings(current_round_number)

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
                weight = calc_weight(judge_i, pairing_i)
                
                gov_rejudges = sum(1 for r in judge.judges.all() 
                                if r.gov_team_id == pairing.gov_team.id)
                opp_rejudges = sum(1 for r in judge.judges.all() 
                                if r.opp_team_id == pairing.opp_team.id)
                
                total_rejudges = gov_rejudges + opp_rejudges
                
                if total_rejudges > 0:
                    rejudge_penalty = total_rejudges * -1000
                    rank_penalty = judge_i * -10
                    weight += (rejudge_penalty + rank_penalty)
                
                edge = (
                    pairing_i,
                    num_rounds + judge_i,
                    weight,
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


def add_outround_judges(round_type=Outround.VARSITY):
    num_teams = Outround.objects.filter(type_of_round=round_type
                                        ).aggregate(Min("num_teams"))["num_teams__min"]

    # First clear any existing judge assignments
    Outround.judges.through.objects.filter(
        outround__type_of_round=round_type,
        outround__num_teams=num_teams
    ).delete()

    judges = list(
        Judge.objects.filter(
            checkin__round_number=0
        ).prefetch_related(
            "judges",  # poorly named relation for the round
            "scratches",
        )
    )
    pairings = tab_logic.sorted_pairings(num_teams, outround=True)
    pairings = [p for p in pairings if p.type_of_round == round_type]
    # Try to have consistent ordering with the round display
    random.seed(1337)
    random.shuffle(pairings)
    random.seed(1337)
    random.shuffle(judges)

    # Order the judges and pairings by power ranking
    judges = sorted(judges, key=lambda j: j.rank, reverse=True)

    # Sorting done by higest position in bracket
    pairings.sort(
        key=lambda x: min(x.gov_team.breaking_team.effective_seed,
                          x.opp_team.breaking_team.effective_seed), reverse=True
    )

    num_rounds = len(pairings)
    judge_round_joins = []

    if round_type == Outround.VARSITY:
        panel_size = TabSettings.get("", 3)
    else:
        panel_size = TabSettings.get("nov_panel_size", 1)

    # Create a working copy of judges for assignment
    available_judges = judges.copy()

    graph_edges = []
    for judge_i, judge in enumerate(available_judges):
        for pairing_i, pairing in enumerate(pairings):
            if not judge_conflict(judge, pairing.gov_team, pairing.opp_team, float('inf')):
                weight = calc_weight(judge_i, pairing_i)
                edge = (
                    pairing_i,
                    num_rounds + judge_i,
                    weight,
                )
                graph_edges.append(edge)
    # Iterate once for each member of the panel
    for panel_member in range(panel_size):
        judge_assignments = mwmatching.maxWeightMatching(graph_edges,
                                                         maxcardinality=True)

        # If there is no possible assignment of judges, raise an error
        if -1 in judge_assignments[:num_rounds] or (num_rounds > 0 and not graph_edges):
            if not graph_edges:
                raise errors.JudgeAssignmentError(
                    "Impossible to assign judges."
                )
            elif -1 in judge_assignments[:num_rounds]:
                pairing_list = judge_assignments[: len(pairings)]
                bad_pairing = pairings[pairing_list.index(-1)]
                raise errors.JudgeAssignmentError(
                    f"Could not find a judge for: {bad_pairing}"
                )
            else:
                raise errors.JudgeAssignmentError()

        # Track which judges to remove after this iteration
        judges_to_remove = []

        # Because we can't bulk-update the judges field of rounds (it's many-to-many),
        # we use the join table model and bulk-create it
        for pairing_i, padded_judge_i in enumerate(judge_assignments[:num_rounds]):
            judge_i = padded_judge_i - num_rounds

            round_obj = pairings[pairing_i]
            judge = available_judges[judge_i]

            if panel_member == 0:
                round_obj.chair = judge

            judge_round_joins.append(Outround.judges.through(judge=judge,
                                                             outround=round_obj))
            judges_to_remove.append(padded_judge_i)

        #Remove edges for already assigned judges rather than re-calculating weights
        graph_edges = [edge for edge in graph_edges if edge[1] not in judges_to_remove]

    # Save the judges to the pairings
    Outround.objects.bulk_update(pairings, ["chair"])
    Outround.judges.through.objects.bulk_create(judge_round_joins)

def calc_weight(judge_i, pairing_i):
    """Calculate the relative badness of this judge assignment

    We want small negative numbers to be preferred to large negative numbers

    """
    return -1 * abs(judge_i - (-1 * pairing_i))


def judge_conflict(judge, team1, team2, max_rejudges=None):
    if max_rejudges is None:
        max_rejudges = TabSettings.get("max_rejudges", 1)
    return (
        any(
            s.team_id
            in (
                team1.id,
                team2.id,
            )
            for s in judge.scratches.all()
        )
        or had_judge(judge, team1, max_rejudges)
        or had_judge(judge, team2, max_rejudges)
    )


def had_judge(judge, team, max_rejudges=1):
    rejudges = 0
    for round_obj in judge.judges.all():
        if round_obj.gov_team_id == team.id or round_obj.opp_team_id == team.id:
            rejudges += 1
    return rejudges >= max_rejudges


def can_judge_teams(list_of_judges, team1, team2):
    result = []
    for judge in list_of_judges:
        if not judge_conflict(judge, team1, team2):
            result.append(judge)
    return result
