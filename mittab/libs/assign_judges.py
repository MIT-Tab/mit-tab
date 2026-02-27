import random
from types import SimpleNamespace
from django.db.models import Min
from mittab.libs import tab_logic, mwmatching, errors
from mittab.apps.tab.models import *

class JudgePairingMode:
    DEFAULT = 0
    CLASSIC = 1

class InroundRoundPriority:
    STANDARD = 0
    BUBBLE_ROUNDS = 1

class OutroundJudgePairingMode:
    SNAKE_DRAFT = 0
    STRAIGHT = 1

class OutroundRoundPriority:
    TOP_OF_BRACKET = 0
    MIDDLE_OF_BRACKET = 1

class OutroundJudgePriority:
    VARSITY_WINGS = 0
    NOVICE_CHAIRS = 1

class WingPairingMode:
    HELP_CHAIRS = 0
    WING_LEARNING = 1
    RANDOM = 2


def get_inround_settings():
    return SimpleNamespace(
        mode=TabSettings.get("judge_pairing_mode", JudgePairingMode.DEFAULT),
        pair_wings=TabSettings.get("pair_wings", True),
        wing_mode=TabSettings.get("wing_pairing_mode", WingPairingMode.HELP_CHAIRS),
        round_priority=TabSettings.get(
            "inround_round_priority",
            InroundRoundPriority.STANDARD,
        ),
        allow_rejudges=TabSettings.get("allow_rejudges", False),
        rejudge_penalty=TabSettings.get("rejudge_penalty", 100),
    )


def get_outround_settings(round_type):
    panel_size = (
        TabSettings.get("var_panel_size", 3)
        if round_type == Outround.VARSITY
        else TabSettings.get("nov_panel_size", 3)
    )
    return SimpleNamespace(
        mode=TabSettings.get("judge_pairing_mode", JudgePairingMode.DEFAULT),
        draft_mode=TabSettings.get(
            "outs_judge_pairing_mode",
            OutroundJudgePairingMode.SNAKE_DRAFT,
        ),
        round_priority=TabSettings.get(
            "outs_round_priority",
            OutroundRoundPriority.TOP_OF_BRACKET,
        ),
        panel_size=panel_size,
        judge_priority=TabSettings.get(
            "outround_judge_priority",
            OutroundJudgePriority.VARSITY_WINGS,
        ),
    )


def sort_outround_pairings(pairings, round_priority):
    seed_func = (
        max
        if round_priority == OutroundRoundPriority.MIDDLE_OF_BRACKET
        else min
    )
    return sorted(
        pairings,
        key=lambda x: seed_func(
            x.gov_team.breaking_team.effective_seed,
            x.opp_team.breaking_team.effective_seed
        )
    )

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
    round_number = TabSettings.get("cur_round") - 1
    settings = get_inround_settings()

    Round.judges.through.objects.filter(
        round__round_number=round_number
    ).delete()
    ManualJudgeAssignment.objects.filter(
        round__round_number=round_number
    ).delete()
    # Get all checked-in judges
    all_judges = list(
        Judge.objects.filter(
            checkin__round_number=round_number
        ).prefetch_related(
            "judges",  # poorly named relation for the round
            "scratches",
            "schools",
        )
    )

    # Sort all_judges once before creating filtered subsets
    random.seed(1337)
    random.shuffle(all_judges)
    all_judges = sorted(all_judges, key=lambda j: j.rank, reverse=True)

    chairs = [j for j in all_judges if not j.wing_only]

    pairings = tab_logic.sorted_pairings(round_number)

    random.seed(1337)
    random.shuffle(pairings)

    chair_scores = construct_judge_scores(chairs, settings.mode)

    bubble_priority = settings.round_priority == InroundRoundPriority.BUBBLE_ROUNDS
    if bubble_priority and round_number > 1:
        bubble_rounds = [p for p in pairings if is_bubble_round(p, round_number)]
        non_bubble_rounds = [p for p in pairings if p not in bubble_rounds]

        def pairing_sort_key(pairing):
            return tab_logic.team_comp(pairing, round_number)

        bubble_rounds.sort(key=pairing_sort_key, reverse=True)
        non_bubble_rounds.sort(key=pairing_sort_key, reverse=True)
        pairings = bubble_rounds + non_bubble_rounds
    else:
        pairings.sort(
            key=lambda x: tab_logic.team_comp(x, round_number), reverse=True
        )

    num_rounds = len(pairings)
    all_teams = []
    for pairing in pairings:
        all_teams.extend((pairing.gov_team, pairing.opp_team))
    rejudge_counts = {}
    if settings.allow_rejudges:
        rejudge_counts = judge_team_rejudge_counts(chairs, all_teams)

    graph_edges = []
    for chair_i, chair in enumerate(chairs):
        chair_score = chair_scores[chair_i]

        for pairing_i, pairing in enumerate(pairings):
            has_conflict = judge_conflict(
                chair,
                pairing.gov_team,
                pairing.opp_team,
                settings.allow_rejudges,
            )
            if has_conflict:
                continue
            weight = calc_weight(chair_score, pairing_i, settings.mode)
            judge_counts = rejudge_counts.get(chair.id)
            rejudge_sum = 0
            if judge_counts:
                rejudge_sum = (
                    judge_counts.get(pairing.gov_team.id, 0)
                    + judge_counts.get(pairing.opp_team.id, 0)
                )
            if rejudge_sum > 0 and settings.rejudge_penalty > 0:
                penalty = settings.rejudge_penalty * (1 + 0.1 * chair_score)
                weight -= penalty * rejudge_sum

            graph_edges.append((pairing_i, num_rounds + chair_i, weight))
    judge_assignments = mwmatching.maxWeightMatching(graph_edges, maxcardinality=True)

    if -1 in judge_assignments[:num_rounds] or (num_rounds > 0 and not graph_edges):
        if not graph_edges:
            # Check if we have enough judges including wing_only judges
            if len(all_judges) >= num_rounds:
                raise errors.JudgeAssignmentError(
                    "Impossible to assign chairs to all rounds. You have enough "
                    "checked-in judges, but some are marked as wing-only and cannot "
                    "chair. Either check in more non-wing judges or unmark some "
                    "wing-only judges to allow them to chair."
                )
            else:
                raise errors.JudgeAssignmentError(
                    "Impossible to assign judges, consider reducing your gaps if you"
                    " are making panels, otherwise find some more judges."
                )
        elif -1 in judge_assignments[:num_rounds]:
            pairing_list = judge_assignments[: len(pairings)]
            bad_pairing = pairings[pairing_list.index(-1)]
            # Check if we have enough judges including wing_only judges
            if len(all_judges) >= num_rounds and len(chairs) < num_rounds:
                raise errors.JudgeAssignmentError(
                    "Impossible to assign chairs to all rounds. You have enough "
                    "checked-in judges, but some are marked as wing-only and cannot "
                    "chair. Either check in more non-wing judges or unmark some "
                    "wing-only judges to allow them to chair."
                )
            else:
                raise errors.JudgeAssignmentError(
                    f"Could not find a judge for: {bad_pairing}"
                )
        else:
            raise errors.JudgeAssignmentError()

    judge_round_joins, chair_by_pairing = [], [None] * num_rounds
    assigned_judge_objects = set()  # Track actual Judge objects by ID
    for pairing_i, padded_chair_i in enumerate(judge_assignments[:num_rounds]):
        chair_i = padded_chair_i - num_rounds

        round_obj = pairings[pairing_i]
        chair = chairs[chair_i]

        round_obj.chair = chair
        chair_by_pairing[pairing_i] = chair_i
        assigned_judge_objects.add(chair.id)  # Track by judge ID
        judge_round_joins.append(
            Round.judges.through(judge=chair, round=round_obj)
        )

    Round.objects.bulk_update(pairings, ["chair"])
    if settings.pair_wings and num_rounds and len(all_judges) > num_rounds:
        max_per_round = min(3, len(all_judges) // num_rounds + 1)
        for _ in range(1, max_per_round):
            # Build wing pool from all_judges excluding already assigned
            wing_judges = [j for j in all_judges if j.id not in assigned_judge_objects]
            if not wing_judges:
                break

            wing_pool = list(range(len(wing_judges)))
            pairing_indices = list(range(num_rounds))
            if settings.wing_mode == WingPairingMode.RANDOM:
                random.shuffle(wing_pool)
                random.shuffle(pairing_indices)

            wing_edges = []
            wing_judge_scores = construct_judge_scores(wing_judges, settings.mode)
            for relative_rank, wing_judge_i in enumerate(wing_pool):
                judge = wing_judges[wing_judge_i]
                judge_score = wing_judge_scores[wing_judge_i]
                for pairing_i in pairing_indices:
                    pairing = pairings[pairing_i]
                    has_conflict = judge_conflict(
                        judge,
                        pairing.gov_team,
                        pairing.opp_team,
                        settings.allow_rejudges,
                    )
                    if has_conflict:
                        continue
                    judge_counts = rejudge_counts.get(judge.id)
                    rejudge_sum = 0
                    if judge_counts:
                        rejudge_sum = (
                            judge_counts.get(pairing.gov_team.id, 0)
                            + judge_counts.get(pairing.opp_team.id, 0)
                        )
                    weight = calc_weight(
                        judge_score,
                        pairing_i,
                        settings.mode,
                        num_rounds=num_rounds,
                        is_assigning_wings=True,
                        wing_mode=settings.wing_mode,
                        chair_judge_i=chair_by_pairing[pairing_i],
                        relative_judge_rank=relative_rank,
                        judge_index=wing_judge_i,
                    )
                    if rejudge_sum > 0 and settings.rejudge_penalty > 0:
                        penalty = settings.rejudge_penalty * (1 + 0.1 * judge_score)
                        weight -= penalty * rejudge_sum

                    wing_edges.append((pairing_i, num_rounds + wing_judge_i, weight))

            if not wing_edges:
                break

            wing_matches = mwmatching.maxWeightMatching(wing_edges, maxcardinality=True)
            for pairing_i, padded_wing_judge_i in enumerate(wing_matches[:num_rounds]):
                if padded_wing_judge_i == -1:
                    continue
                wing_judge_i = padded_wing_judge_i - num_rounds
                judge = wing_judges[wing_judge_i]
                if judge.id in assigned_judge_objects:
                    continue
                judge_round_joins.append(
                    Round.judges.through(
                        judge=judge,
                        round=pairings[pairing_i],
                    )
                )
                assigned_judge_objects.add(judge.id)

    Round.judges.through.objects.bulk_create(judge_round_joins)


def _normalize_round_specs(round_specs=None, round_type=None):
    if round_specs is None:
        if round_type is None:
            round_type = Outround.VARSITY
        num_teams = Outround.objects.filter(
            type_of_round=round_type
        ).aggregate(Min("num_teams"))["num_teams__min"]
        if num_teams is None:
            return []
        round_specs = [(round_type, int(num_teams))]

    valid_round_types = dict(Outround.TYPE_OF_ROUND_CHOICES)
    deduped = []
    seen = set()
    for spec in round_specs:
        try:
            type_of_round, num_teams = int(spec[0]), int(spec[1])
        except (TypeError, ValueError, IndexError):
            continue
        if type_of_round not in valid_round_types:
            continue
        key = (type_of_round, num_teams)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


def _ordered_pairings_for_slot(pairings, panel_member, snake_draft_mode):
    ordered = list(pairings)
    if snake_draft_mode and panel_member % 2 == 1:
        ordered.reverse()
    return ordered


def _build_active_pairings(
        round_specs,
        pairings_by_spec,
        panel_size_by_spec,
        panel_member,
        snake_draft_mode,
        force_novice_last):
    active_specs = [
        spec for spec in round_specs
        if panel_member < panel_size_by_spec.get(spec, 0)
    ]

    if force_novice_last:
        varsity_specs = [s for s in active_specs if s[0] == Outround.VARSITY]
        novice_specs = [s for s in active_specs if s[0] == Outround.NOVICE]
        ordered_specs = varsity_specs + novice_specs
    else:
        ordered_specs = active_specs

    active_pairings = []
    for spec in ordered_specs:
        active_pairings.extend(
            _ordered_pairings_for_slot(
                pairings_by_spec.get(spec, []),
                panel_member,
                snake_draft_mode,
            )
        )
    return active_pairings


def _assign_outround_judges_for_specs(
        round_specs,
        pairings_by_spec,
        panel_size_by_spec,
        judges,
        judge_scores,
        settings,
        available_indices,
        force_novice_last=False):
    link_outround = Outround.judges.through
    judge_round_joins = []

    max_panel_size = max(panel_size_by_spec.values()) if panel_size_by_spec else 0
    snake_draft_mode = settings.draft_mode == OutroundJudgePairingMode.SNAKE_DRAFT

    for panel_member in range(max_panel_size):
        active_pairings = _build_active_pairings(
            round_specs=round_specs,
            pairings_by_spec=pairings_by_spec,
            panel_size_by_spec=panel_size_by_spec,
            panel_member=panel_member,
            snake_draft_mode=snake_draft_mode,
            force_novice_last=force_novice_last,
        )
        num_rounds = len(active_pairings)
        if num_rounds == 0:
            continue

        graph_edges = []
        for judge_i in available_indices:
            judge = judges[judge_i]
            for pairing_i, pairing in enumerate(active_pairings):
                has_conflict = judge_conflict(
                    judge,
                    pairing.gov_team,
                    pairing.opp_team,
                    True,
                )
                if has_conflict:
                    continue
                weight = calc_weight(
                    judge_scores[judge_i],
                    pairing_i,
                    settings.mode,
                    num_rounds=num_rounds,
                )
                graph_edges.append((pairing_i, num_rounds + judge_i, weight))

        panel_matches = mwmatching.maxWeightMatching(graph_edges, maxcardinality=True)
        if -1 in panel_matches[:num_rounds] or (num_rounds > 0 and not graph_edges):
            if not graph_edges:
                raise errors.JudgeAssignmentError("Impossible to assign judges.")
            pairing_list = panel_matches[:len(active_pairings)]
            bad_pairing = active_pairings[pairing_list.index(-1)]
            raise errors.JudgeAssignmentError(
                f"Could not find a judge for: {bad_pairing}"
            )

        for pairing_i, padded_judge_i in enumerate(panel_matches[:num_rounds]):
            judge_i = padded_judge_i - num_rounds
            round_obj = active_pairings[pairing_i]
            judge = judges[judge_i]

            if panel_member == 0:
                round_obj.chair = judge

            judge_round_joins.append(link_outround(judge=judge, outround=round_obj))
            available_indices.remove(judge_i)

    return judge_round_joins


def _collect_outround_pairing_data(round_specs, round_priority):
    pairings_by_spec = {}
    panel_size_by_spec = {}

    for round_type, num_teams in round_specs:
        pairings = tab_logic.sorted_pairings(num_teams, outround=True)
        pairings = [
            p for p in pairings
            if p.type_of_round == round_type and p.victor == Outround.UNKNOWN
        ]
        pairings_by_spec[(round_type, num_teams)] = sort_outround_pairings(
            pairings,
            round_priority,
        )
        panel_size_by_spec[(round_type, num_teams)] = (
            TabSettings.get("var_panel_size", 3)
            if round_type == Outround.VARSITY
            else TabSettings.get("nov_panel_size", 3)
        )

    return pairings_by_spec, panel_size_by_spec


def add_outround_judges(round_type=Outround.VARSITY, round_specs=None):
    normalized_specs = _normalize_round_specs(round_specs, round_type=round_type)
    if not normalized_specs:
        return

    settings = get_outround_settings(normalized_specs[0][0])
    pairings_by_spec, panel_size_by_spec = _collect_outround_pairing_data(
        normalized_specs,
        settings.round_priority,
    )

    active_round_ids = [
        pairing.id
        for pairings in pairings_by_spec.values()
        for pairing in pairings
    ]
    if not active_round_ids:
        return

    # Clear existing assignments only for undecided rounds in the selected scope.
    Outround.judges.through.objects.filter(outround_id__in=active_round_ids).delete()
    Outround.objects.filter(id__in=active_round_ids).update(chair=None)

    judges = list(
        Judge.objects.filter(
            checkin__round_number=0
        ).prefetch_related(
            "judges",
            "scratches",
            "schools",
        )
    )
    random.seed(1337)
    random.shuffle(judges)
    judges = sorted(judges, key=lambda j: j.rank, reverse=True)
    judge_scores = construct_judge_scores(judges)
    available_indices = list(range(len(judges)))

    has_varsity = any(spec[0] == Outround.VARSITY for spec in normalized_specs)
    has_novice = any(spec[0] == Outround.NOVICE for spec in normalized_specs)
    run_joint_novice_chairs = (
        has_varsity
        and has_novice
        and settings.judge_priority == OutroundJudgePriority.NOVICE_CHAIRS
    )

    judge_round_joins = []
    if run_joint_novice_chairs:
        judge_round_joins.extend(
            _assign_outround_judges_for_specs(
                round_specs=normalized_specs,
                pairings_by_spec=pairings_by_spec,
                panel_size_by_spec=panel_size_by_spec,
                judges=judges,
                judge_scores=judge_scores,
                settings=settings,
                available_indices=available_indices,
                force_novice_last=True,
            )
        )
    else:
        varsity_specs = [s for s in normalized_specs if s[0] == Outround.VARSITY]
        novice_specs = [s for s in normalized_specs if s[0] == Outround.NOVICE]
        for specs in (varsity_specs, novice_specs):
            if not specs:
                continue
            judge_round_joins.extend(
                _assign_outround_judges_for_specs(
                    round_specs=specs,
                    pairings_by_spec=pairings_by_spec,
                    panel_size_by_spec=panel_size_by_spec,
                    judges=judges,
                    judge_scores=judge_scores,
                    settings=settings,
                    available_indices=available_indices,
                    force_novice_last=False,
                )
            )

    rounds_to_update = []
    for round_type, num_teams in normalized_specs:
        rounds_to_update.extend(pairings_by_spec.get((round_type, num_teams), []))

    if rounds_to_update:
        Outround.objects.bulk_update(rounds_to_update, ["chair"])
    if judge_round_joins:
        Outround.judges.through.objects.bulk_create(judge_round_joins)

def calc_weight(
        judge_i,
        pairing_i,
        mode=JudgePairingMode.DEFAULT,
        num_rounds=None,
        is_assigning_wings=False,
        wing_mode=WingPairingMode.HELP_CHAIRS,
        chair_judge_i=None,
        relative_judge_rank=None,
        judge_index=None,
):
    """Calculate the relative badness of this judge assignment"""
    effective_judge_pos = judge_i

    if is_assigning_wings:
        if relative_judge_rank is not None:
            effective_judge_pos = relative_judge_rank
        if wing_mode == WingPairingMode.HELP_CHAIRS and num_rounds:
            effective_judge_pos = num_rounds - effective_judge_pos - 1
        elif wing_mode == WingPairingMode.RANDOM and num_rounds:
            seed_base = judge_index if judge_index is not None else judge_i
            random.seed(seed_base * 1000 + pairing_i)
            effective_judge_pos = random.randint(0, num_rounds - 1)

    if mode == JudgePairingMode.CLASSIC:
        base_weight = -1 * abs(effective_judge_pos - (-1 * pairing_i))
    else:
        delta = effective_judge_pos - pairing_i
        base_weight = 0 if delta <= 0 else -1 * (delta ** 2)

    help_chairs_bonus = (
        is_assigning_wings
        and wing_mode == WingPairingMode.HELP_CHAIRS
        and chair_judge_i is not None
        and judge_index is not None
        and judge_index < chair_judge_i
    )
    if help_chairs_bonus:
        base_weight += (chair_judge_i - judge_index) * 10

    return base_weight


def judge_conflict(judge, team1, team2, allow_rejudges=None):
    if allow_rejudges is None:
        allow_rejudges = TabSettings.get("allow_rejudges", False)

    has_scratches = any(
        s.team_id in (team1.id, team2.id)
        for s in judge.scratches.all()
    )

    has_school_conflict = any(
        team.school in judge.schools.all() or
        (team.hybrid_school in judge.schools.all())
        for team in (team1, team2)
    )

    if not allow_rejudges:
        return (
            has_scratches
            or has_school_conflict
            or had_judge(judge, team1)
            or had_judge(judge, team2)
        )
    else:
        return has_scratches or has_school_conflict

def is_bubble_round(pairing, round_number):
    gov_losses = round_number - tab_logic.stats.tot_wins(pairing.gov_team)
    opp_losses = round_number - tab_logic.stats.tot_wins(pairing.opp_team)
    return (gov_losses == 1) or (opp_losses == 1)


def had_judge(judge, team):
    for round_obj in judge.judges.all():
        if round_obj.gov_team_id == team.id or round_obj.opp_team_id == team.id:
            return True
    return False


def can_judge_teams(list_of_judges, team1, team2, allow_rejudges=None):
    result = []
    for judge in list_of_judges:
        if not judge_conflict(judge, team1, team2, allow_rejudges=allow_rejudges):
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
