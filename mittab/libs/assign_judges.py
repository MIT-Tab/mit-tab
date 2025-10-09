import random
from django.db.models import Min
from mittab.libs import tab_logic, mwmatching, errors
from mittab.apps.tab.models import *

class JudgePairingMode:
    DEFAULT = 0
    CLASSIC = 1

class OutsJudgePairingMode:
    SnakeDraft = 0
    Straight = 1

class OutsRoundPriorityMode:
    TopOfBracket = 0
    MiddleOfBracket = 1


def construct_judge_scores(judges, mode=JudgePairingMode.DEFAULT):
    if mode == JudgePairingMode.CLASSIC:
        return list(range(len(judges)))
    judge_scores = []
    judge_score = -1
    previous_rank = 0
    for judge_i, judge in enumerate(judges):
        if previous_rank != judge.rank:
            judge_score = judge_i
            previous_rank = judge.rank
        judge_scores.append(judge_score)
    return judge_scores


def add_judges():
    current_round_number = TabSettings.get("cur_round") - 1
    mode = TabSettings.get("judge_pairing_mode", JudgePairingMode.DEFAULT)

    # First clear any existing judge assignments
    Round.judges.through.objects.filter(
        round__round_number=current_round_number
    ).delete()

    judges = list(
        Judge.objects.filter(
            checkin__round_number=current_round_number,
            wing_only=False
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
    judge_scores = construct_judge_scores(judges, mode)
    pairings.sort(
        key=lambda x: tab_logic.team_comp(x, current_round_number), reverse=True
    )

    num_rounds = len(pairings)
    all_teams = []
    for pairing in pairings:
        all_teams.extend([pairing.gov_team, pairing.opp_team])

    rejudge_counts = judge_team_rejudge_counts(judges, all_teams)
    rejudge_penalty = TabSettings.get("rejudge_penalty", 100)
    # Assign chairs (single judges) to each round using perfect pairing
    graph_edges = []
    for judge_i, judge in enumerate(judges):
        judge_score = judge_scores[judge_i]

        for pairing_i, pairing in enumerate(pairings):
            if not judge_conflict(judge, pairing.gov_team, pairing.opp_team):
                weight = calc_weight(judge_score, pairing_i, mode)
                rejudge_sum = 0
                if judge.id in rejudge_counts:
                    rejudge_sum += rejudge_counts[judge.id].get(pairing.gov_team.id, 0)
                    rejudge_sum += rejudge_counts[judge.id].get(pairing.opp_team.id, 0)

                if rejudge_sum > 0:
                    weight -= rejudge_penalty * (1 + .1 * judge_score) * rejudge_sum

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
    mode = TabSettings.get("judge_pairing_mode", JudgePairingMode.DEFAULT)
    outround_judge_mode = TabSettings.get("outs_judge_pairing_mode",
                                          OutsJudgePairingMode.SnakeDraft)
    outround_round_mode = TabSettings.get("outs_round_priority",
                                          OutsRoundPriorityMode.TopOfBracket)

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
    judge_scores = construct_judge_scores(judges)

    if outround_round_mode == OutsRoundPriorityMode.TopOfBracket:
        pairings.sort(
            key=lambda x: min(x.gov_team.breaking_team.effective_seed,
                              x.opp_team.breaking_team.effective_seed)
        )
    else:
        pairings.sort(
            key=lambda x: max(x.gov_team.breaking_team.effective_seed,
                              x.opp_team.breaking_team.effective_seed)
        )

    num_rounds = len(pairings)
    judge_round_joins = []

    if round_type == Outround.VARSITY:
        panel_size = TabSettings.get("var_panel_size", 3)
    else:
        panel_size = TabSettings.get("nov_panel_size", 1)

    # Create a working copy of judges for assignment
    available_judges = judges.copy()


    # Iterate once for each member of the panel
    for panel_member in range(panel_size):
        graph_edges = []
        for judge_i, judge in enumerate(available_judges):
            for pairing_i, pairing in enumerate(pairings):
                if not judge_conflict(judge, pairing.gov_team, pairing.opp_team, True):
                    effective_pairing_i = pairing_i
                    if (
                            outround_judge_mode == OutsJudgePairingMode.SnakeDraft
                            and panel_member % 2 == 1
                    ):
                        effective_pairing_i = num_rounds - 1 - pairing_i
                    weight = calc_weight(judge_scores[judge_i],
                                         effective_pairing_i, mode)
                    edge = (
                        pairing_i,
                        num_rounds + judge_i,
                        weight,
                    )
                    graph_edges.append(edge)
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

        # Remove assigned judges from available pool
        available_judges = [j for i, j in enumerate(available_judges)
                            if num_rounds + i not in judges_to_remove]

    # Save the judges to the pairings
    Outround.objects.bulk_update(pairings, ["chair"])
    Outround.judges.through.objects.bulk_create(judge_round_joins)

def calc_weight(judge_i, pairing_i, mode=JudgePairingMode.DEFAULT):
    """Calculate the relative badness of this judge assignment"""
    if mode == JudgePairingMode.CLASSIC:
        return -1 * abs(judge_i - (-1 * pairing_i))

    delta = judge_i - pairing_i
    if delta <= 0:
        return 0
    return -1 * (delta ** 2)


def judge_conflict(judge, team1, team2, allow_rejudges=None):
    if allow_rejudges is None:
        allow_rejudges = TabSettings.get("allow_rejudges", False)
    has_scratches = any(
        s.team_id in (team1.id, team2.id)
        for s in judge.scratches.all()
    )
    if not allow_rejudges:
        return (
            has_scratches
            or had_judge(judge, team1)
            or had_judge(judge, team2)
        )
    else:
        return has_scratches


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

def judge_team_rejudge_counts(judges, teams, exclude_round_id=None):
    """Judges must have prefetch_related('judges') to prevent N+1
        before calling this function"""
    result = {}
    team_ids = [team.id for team in teams]

    for judge in judges:
        result[judge.id] = {}
        for round_obj in judge.judges.all():
            if exclude_round_id and round_obj.id == exclude_round_id:
                continue

            if round_obj.gov_team_id in team_ids:
                result[judge.id][round_obj.gov_team_id] = result[judge.id].get(
                    round_obj.gov_team_id, 0) + 1
            if round_obj.opp_team_id in team_ids:
                result[judge.id][round_obj.opp_team_id] = result[judge.id].get(
                    round_obj.opp_team_id, 0) + 1

    return result
