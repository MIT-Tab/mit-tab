import math
import random

from django.db import transaction
from django.db.models import Q

from mittab.apps.tab.models import *
from mittab.apps.tab.live_results import published_team_placements

from mittab.libs.outround_tab_logic.checks import have_enough_rooms, \
    get_break_sizes
from mittab.libs.outround_tab_logic.bracket_generation import gen_bracket
from mittab.libs.tab_logic import (
    have_properly_entered_data,
)
from mittab.libs.tab_logic.rankings import get_team_rankings
from mittab.libs.tab_logic.stats import num_govs
from mittab.libs import errors
import mittab.libs.cacheing.cache_logic as cache_logic


class OutroundPairingMode:
    STANDARD = 0
    CHALLENGE_EACH_ROUND = 1
    CHALLENGE_ONCE = 2


class ChallengeSeedBasis:
    ORIGINAL = 0
    PREVIOUS_ROUND = 1


def _is_power_of_two(value):
    return value > 0 and (value & (value - 1)) == 0


def _challenge_generation(type_of_round):
    return BreakingTeam.objects.filter(type_of_team=type_of_round).order_by(
        "pk",
    ).values_list("pk", flat=True).first() or 0


def _current_challenge_picks(type_of_round, num_teams, team_ids):
    generation_key = f"challenge_pick_generation_{type_of_round}"
    if TabSettings.get(generation_key, 0) != _challenge_generation(type_of_round):
        return OutroundChallenge.objects.none()
    return OutroundChallenge.objects.filter(
        type_of_round=type_of_round, num_teams=num_teams, challenger_id__in=team_ids,
    )


def prepare_challenge_edits(type_of_round):
    generation_key = f"challenge_pick_generation_{type_of_round}"
    generation = _challenge_generation(type_of_round)
    if TabSettings.get(generation_key, 0) != generation:
        OutroundChallenge.objects.filter(type_of_round=type_of_round).delete()
        TabSettings.set(generation_key, generation)


def _setting_key_for_challenge_once(type_of_round):
    generation = _challenge_generation(type_of_round)
    return f"challenge_once_applied_{type_of_round}_{generation}"


def _get_challenge_settings():
    mode = int(TabSettings.get("outround_pairing_mode", OutroundPairingMode.STANDARD))
    if mode not in (OutroundPairingMode.CHALLENGE_EACH_ROUND,
                    OutroundPairingMode.CHALLENGE_ONCE):
        mode = OutroundPairingMode.STANDARD
    return {
        "mode": mode,
        "seed_basis": int(TabSettings.get(
            "challenge_seed_basis",
            ChallengeSeedBasis.PREVIOUS_ROUND,
        )),
    }


def _ordered_breaking_teams(base_queryset, seed_basis):
    order_field = (
        "seed" if seed_basis == ChallengeSeedBasis.ORIGINAL else "effective_seed"
    )
    return list(base_queryset.select_related("team").order_by(order_field, "team__id"))


def challenge_round_enabled(type_of_break, num_teams, actual_num_teams):
    challenge_settings = _get_challenge_settings()
    mode = challenge_settings["mode"]
    if mode == OutroundPairingMode.STANDARD:
        return False
    if num_teams < 4 or actual_num_teams != num_teams:
        return False
    if not _is_power_of_two(num_teams):
        return False
    if mode == OutroundPairingMode.CHALLENGE_ONCE and TabSettings.get(
        _setting_key_for_challenge_once(type_of_break), 0
    ):
        return False
    return True


def _get_challenge_pairs(type_of_break, num_teams, base_queryset, challenge_settings):
    seed_basis = challenge_settings["seed_basis"]
    if challenge_settings["mode"] == OutroundPairingMode.CHALLENGE_ONCE:
        seed_basis = ChallengeSeedBasis.PREVIOUS_ROUND

    ordered = _ordered_breaking_teams(base_queryset, seed_basis)
    remaining = ordered[:]
    picks = {
        entry.challenger_id: entry.challenged_id
        for entry in _current_challenge_picks(
            type_of_break, num_teams, [entry.team_id for entry in ordered],
        )
    }
    picked_ids = list(picks) + list(picks.values())
    if len(picked_ids) != len(set(picked_ids)):
        raise ValueError("Challenge picks must use each team at most once.")
    if any(team_id not in {entry.team.id for entry in ordered}
           for team_id in picked_ids):
        raise ValueError("A challenge pick refers to a team outside the active pool.")
    result = []
    seed_updates = []
    while remaining:
        # A team already selected as an opponent cannot become a challenger.
        eligible_challengers = [entry for entry in remaining
                               if entry.team.id not in set(picks.values())]
        challenger = eligible_challengers[0]
        remaining_ids = {
    entry.team.id for entry in remaining if entry.team.id != challenger.team.id}
        challenged_id = picks.get(challenger.team.id)
        if challenged_id not in remaining_ids:
            # Default challenge policy when no challenge was explicitly entered:
            # challenge the currently lowest-ranked available seed.
            reserved_ids = set(picks) | set(picks.values())
            candidates = [entry for entry in remaining
                          if entry.team.id != challenger.team.id
                          and entry.team.id not in reserved_ids]
            if not candidates:
                raise ValueError("Challenge picks do not leave a valid opponent.")
            challenged = candidates[-1]
        else:
            challenged = next(
    entry for entry in remaining if entry.team.id == challenged_id)

        high_seed = len(result) + 1
        low_seed = num_teams - len(result)
        seed_updates.extend([
            (challenger, high_seed),
            (challenged, low_seed),
        ])
        result.append((challenger, challenged))
        remaining = [entry for entry in remaining
                     if entry.team.id not in {challenger.team.id, challenged.team.id}]
    return result, seed_updates


def get_challenge_setup_context(type_of_break, num_teams):
    challenge_settings = _get_challenge_settings()
    mode = challenge_settings["mode"]
    enabled = mode != OutroundPairingMode.STANDARD and _is_power_of_two(num_teams) \
        and num_teams >= 4
    if mode == OutroundPairingMode.CHALLENGE_ONCE and TabSettings.get(
        _setting_key_for_challenge_once(type_of_break), 0
    ):
        enabled = False
    if not enabled:
        return {"enabled": False}

    lost_outround = [t.loser.id for t in Outround.objects.filter(
        type_of_round=type_of_break) if t.loser]
    base_queryset = BreakingTeam.objects.filter(
        type_of_team=type_of_break
    ).exclude(
        team__id__in=lost_outround
    )
    if base_queryset.count() != num_teams:
        return {"enabled": False}

    seed_basis = challenge_settings["seed_basis"]
    if mode == OutroundPairingMode.CHALLENGE_ONCE:
        seed_basis = ChallengeSeedBasis.PREVIOUS_ROUND

    ordered = _ordered_breaking_teams(base_queryset, seed_basis)
    challenge_map = {
        entry.challenger_id: entry.challenged_id
        for entry in _current_challenge_picks(
            type_of_break, num_teams, [entry.team_id for entry in ordered],
        )
    }
    used_team_ids = set(challenge_map) | set(challenge_map.values())
    pending = [
        entry for entry in ordered
        if entry.team.id not in used_team_ids
    ]

    def team_option(entry):
        seed = (
            entry.seed
            if seed_basis == ChallengeSeedBasis.ORIGINAL
            else entry.effective_seed
        )
        return {
            "id": entry.team.id,
            "name": entry.team.name,
            "seed": seed,
            "label": f"[{seed}] {entry.team.name}",
        }

    team_options = [team_option(entry) for entry in ordered]
    options_by_id = {option["id"]: option for option in team_options}
    saved_challenges = [
        {
            "challenger": options_by_id[entry.team.id],
            "challenged": options_by_id[challenge_map[entry.team.id]],
        }
        for entry in ordered
        if entry.team.id in challenge_map
    ]

    next_challenger = None
    if len(pending) > 2:
        next_challenger = pending[0]
    next_challenger_option = (
        team_option(next_challenger) if next_challenger else None
    )
    opponent_options = [
        team_option(entry)
        for entry in pending
        if not next_challenger or entry.team.id != next_challenger.team.id
    ]
    automatic_pair = (
        [team_option(entry) for entry in pending]
        if len(pending) == 2
        else []
    )

    return {
        "enabled": True,
        "type_of_round": type_of_break,
        "num_teams": num_teams,
        "mode": mode,
        "mode_label": (
            "Challenge Each Round"
            if mode == OutroundPairingMode.CHALLENGE_EACH_ROUND
            else "Challenge Once (Seed Bracket)"
        ),
        "seed_basis_label": (
            "Original Seeds"
            if seed_basis == ChallengeSeedBasis.ORIGINAL
            else "Previous Round Seeds"
        ),
        "team_options": team_options,
        "challenges": [
            {
                "challenger_id": b.team.id,
                "challenger_name": b.team.name,
                "challenged_id": challenge_map.get(b.team.id),
            }
            for b in ordered
        ],
        "remaining_teams": [team_option(entry) for entry in pending],
        "saved_challenges": saved_challenges,
        "next_challenger": next_challenger_option,
        "opponent_options": opponent_options,
        "automatic_pair": automatic_pair,
        "choices_entered": len(saved_challenges),
        "choices_total": max((num_teams // 2) - 1, 0),
    }


@transaction.atomic
def save_challenge_pick(type_of_break, num_teams, challenger_id, challenged_id):
    list(BreakingTeam.objects.select_for_update().filter(type_of_team=type_of_break))
    if challenger_id == challenged_id:
        raise ValueError("A team cannot challenge itself.")

    challenge_context = get_challenge_setup_context(type_of_break, num_teams)
    if not challenge_context.get("enabled"):
        raise ValueError("Challenge entries are not available for this round.")

    valid_team_ids = {option["id"] for option in challenge_context["team_options"]}
    if challenger_id not in valid_team_ids or challenged_id not in valid_team_ids:
        raise ValueError("Selected teams are not in this challenge pool.")

    prepare_challenge_edits(type_of_break)
    conflicting = OutroundChallenge.objects.filter(
        type_of_round=type_of_break,
        num_teams=num_teams,
    ).exclude(challenger_id=challenger_id).filter(
        Q(challenger_id__in=[challenger_id, challenged_id])
        | Q(challenged_id__in=[challenger_id, challenged_id])
    ).exists()
    if conflicting:
        raise ValueError("One of these teams already has a challenge pick.")

    OutroundChallenge.objects.update_or_create(
        type_of_round=type_of_break,
        num_teams=num_teams,
        challenger_id=challenger_id,
        defaults={"challenged_id": challenged_id},
    )


@transaction.atomic
def delete_challenge_pick(type_of_break, num_teams, challenger_id):
    list(BreakingTeam.objects.select_for_update().filter(type_of_team=type_of_break))
    challenge_context = get_challenge_setup_context(type_of_break, num_teams)
    if not challenge_context.get("enabled"):
        raise ValueError("Challenge entries are not available for this round.")

    valid_team_ids = {option["id"] for option in challenge_context["team_options"]}
    if challenger_id not in valid_team_ids:
        raise ValueError("Selected team is not in this challenge pool.")

    prepare_challenge_edits(type_of_break)
    OutroundChallenge.objects.filter(
        type_of_round=type_of_break,
        num_teams=num_teams,
        challenger_id=challenger_id,
    ).delete()


def perform_the_break():
    for division in (BreakingTeam.VARSITY, BreakingTeam.NOVICE):
        published_team_placements(division)
    if _get_challenge_settings()["mode"] == OutroundPairingMode.STANDARD:
        return _perform_the_break_standard()
    with transaction.atomic():
        return _perform_the_break_experimental()


def _perform_the_break_experimental():
    teams, nov_teams = cache_logic.cache_fxn_key(
        get_team_rankings,
        "team_rankings",
        cache_logic.DEFAULT,
        None
    )

    var_teams_to_break, nov_teams_to_break = get_break_sizes()

    if (nov_teams_to_break is None or var_teams_to_break is None or
            nov_teams_to_break < 0 or var_teams_to_break < 0):
        return False, "Please check your break tab settings"

    # This forces a refresh of the breaking teams
    Outround.objects.all().delete()
    OutroundChallenge.objects.all().delete()
    BreakingTeam.objects.all().delete()

    current_seed = 1
    for team in teams:
        if not team[0].break_preference == Team.VARSITY:
            continue

        if current_seed > var_teams_to_break:
            break

        BreakingTeam.objects.create(team=team[0],
                                    seed=current_seed,
                                    effective_seed=current_seed,
                                    type_of_team=BreakingTeam.VARSITY)
        current_seed += 1

    current_seed = 1
    for nov_team in nov_teams:
        if current_seed > nov_teams_to_break:
            break

        if BreakingTeam.objects.filter(team=nov_team[0]).exists():
            continue

        BreakingTeam.objects.create(team=nov_team[0],
                                    seed=current_seed,
                                    effective_seed=current_seed,
                                    type_of_team=BreakingTeam.NOVICE)

        current_seed += 1

    for round_type, size in [(BreakingTeam.VARSITY, var_teams_to_break),
                             (BreakingTeam.NOVICE, nov_teams_to_break)]:
        if not size:
            continue
        actual_size = BreakingTeam.objects.filter(type_of_team=round_type).count()
        if not challenge_round_enabled(round_type, size, actual_size):
            pair(round_type)

    return True, "Success!"


def is_pairing_possible(num_teams):
    if num_teams > Team.objects.count():
        raise errors.NotEnoughTeamsError()

    if num_teams < 2:
        raise errors.NotEnoughTeamsError()

    # Check there are enough rooms
    if not have_enough_rooms()[0]:
        raise errors.NotEnoughRoomsError()

    # If we have results, they should be entered and there should be no
    # byes or noshows for teams that debated
    round_to_check = TabSettings.get("tot_rounds", 5)
    have_properly_entered_data(round_to_check)


def gov_team(team_one, team_two):
    """
    Determine which team should be gov in an outround pairing.

    Priority order:
    1. Sidelock - if teams have debated before, they must be on opposite sides
    2. Least govs - team with fewer gov rounds gets gov
    3. Random - if gov counts are equal

    Returns:
        (sidelock: bool, gov_team: BreakingTeam)
    """
    sidelock = TabSettings.get("sidelock", 0)

    # 1. Check for sidelock
    if sidelock:
        if Round.objects.filter(gov_team=team_one.team,
                                opp_team=team_two.team).exists():
            return True, team_two
        elif Round.objects.filter(gov_team=team_two.team,
                                  opp_team=team_one.team).exists():
            return True, team_one

    # 2. Check for least govs
    team_one_govs = num_govs(team_one.team)
    team_two_govs = num_govs(team_two.team)

    if team_one_govs < team_two_govs:
        return False, team_one
    elif team_two_govs < team_one_govs:
        return False, team_two

    # 3. Random assignment (govs are equal)
    if random.randint(0, 1) == 0:
        return False, team_one
    else:
        return False, team_two


def pair(type_of_break=BreakingTeam.VARSITY):
    published_team_placements(type_of_break)
    if _get_challenge_settings()["mode"] == OutroundPairingMode.STANDARD:
        return _pair_standard(type_of_break)
    with transaction.atomic():
        return _pair_experimental(type_of_break)


def _pair_experimental(type_of_break=BreakingTeam.VARSITY):
    list(BreakingTeam.objects.select_for_update().filter(type_of_team=type_of_break))
    lost_outround = [t.loser.id for t in Outround.objects.filter(
        type_of_round=type_of_break) if t.loser]

    base_queryset = BreakingTeam.objects.filter(
        type_of_team=type_of_break
    ).exclude(
        team__id__in=lost_outround
    )

    actual_num_teams = base_queryset.count()
    num_teams = actual_num_teams

    teams_for_bracket = num_teams

    while not math.log(teams_for_bracket, 2) % 1 == 0:
        teams_for_bracket += 1

    num_teams = teams_for_bracket

    is_pairing_possible(num_teams)

    Outround.objects.filter(
        num_teams=num_teams
    ).filter(
        type_of_round=type_of_break
    ).all().delete()

    if type_of_break == BreakingTeam.VARSITY:
        TabSettings.set("var_outrounds_public", 0)
    else:
        TabSettings.set("nov_outrounds_public", 0)

    challenge_settings = _get_challenge_settings()
    challenge_pairs = None
    if challenge_round_enabled(type_of_break, num_teams, actual_num_teams):
        challenge_pairs, seed_updates = _get_challenge_pairs(
            type_of_break,
            num_teams,
            base_queryset,
            challenge_settings,
        )
        for breaking_team, new_seed in seed_updates:
            if breaking_team.effective_seed != new_seed:
                breaking_team.effective_seed = new_seed
                breaking_team.save()

    if challenge_pairs is not None:
        pairings = challenge_pairs
    else:
        bracket = gen_bracket(num_teams)
        pairings = []
        for pairing in bracket:
            team_one = base_queryset.filter(
                effective_seed=pairing[0]).prefetch_related(
                    "team__gov_team",
                    "team__gov_team_outround").first()
            team_two = base_queryset.filter(
                effective_seed=pairing[1]).prefetch_related(
                    "team__gov_team",
                    "team__gov_team_outround").first()
            pairings.append((team_one, team_two))

    for team_one, team_two in pairings:
        if not team_one or not team_two:
            continue

        sidelock, gov = gov_team(team_one, team_two)
        opp = team_one if gov == team_two else team_two

        Outround.objects.create(
            num_teams=num_teams,
            type_of_round=type_of_break,
            gov_team=gov.team,
            opp_team=opp.team,
            sidelock=sidelock
        )

    if (challenge_settings["mode"] == OutroundPairingMode.CHALLENGE_ONCE
            and challenge_pairs):
        TabSettings.set(_setting_key_for_challenge_once(type_of_break), 1)


def _pair_standard(type_of_break=BreakingTeam.VARSITY):
    lost_outround = [t.loser.id for t in Outround.objects.all() if t.loser]

    base_queryset = BreakingTeam.objects.filter(
        type_of_team=type_of_break
    ).exclude(
        team__id__in=lost_outround
    )

    num_teams = base_queryset.count()

    teams_for_bracket = num_teams

    while not math.log(teams_for_bracket, 2) % 1 == 0:
        teams_for_bracket += 1

    num_teams = teams_for_bracket

    is_pairing_possible(num_teams)

    Outround.objects.filter(
        num_teams=num_teams
    ).filter(
        type_of_round=type_of_break
    ).all().delete()

    if type_of_break == BreakingTeam.VARSITY:
        TabSettings.set("var_outrounds_public", 0)
    else:
        TabSettings.set("nov_outrounds_public", 0)

    bracket = gen_bracket(num_teams)

    for pairing in bracket:
        team_one = base_queryset.filter(
            effective_seed=pairing[0]).prefetch_related(
                "team__gov_team",
                "team__gov_team_outround").first()
        team_two = base_queryset.filter(
            effective_seed=pairing[1]).prefetch_related(
                "team__gov_team",
                "team__gov_team_outround").first()

        if not team_one or not team_two:
            continue

        sidelock, gov = gov_team(team_one, team_two)
        opp = team_one if gov == team_two else team_two

        Outround.objects.create(
            num_teams=num_teams,
            type_of_round=type_of_break,
            gov_team=gov.team,
            opp_team=opp.team,
            sidelock=sidelock
        )


def _perform_the_break_standard():
    teams, nov_teams = cache_logic.cache_fxn_key(
        get_team_rankings,
        "team_rankings",
        cache_logic.DEFAULT,
        None
    )

    var_teams_to_break, nov_teams_to_break = get_break_sizes()

    if (nov_teams_to_break is None or var_teams_to_break is None or
            nov_teams_to_break < 0 or var_teams_to_break < 0):
        return False, "Please check your break tab settings"

    # This forces a refresh of the breaking teams
    Outround.objects.all().delete()
    BreakingTeam.objects.all().delete()

    current_seed = 1
    for team in teams:
        if not team[0].break_preference == Team.VARSITY:
            continue

        if current_seed > var_teams_to_break:
            break

        BreakingTeam.objects.create(team=team[0],
                                    seed=current_seed,
                                    effective_seed=current_seed,
                                    type_of_team=BreakingTeam.VARSITY)
        current_seed += 1

    current_seed = 1
    for nov_team in nov_teams:
        if current_seed > nov_teams_to_break:
            break

        if BreakingTeam.objects.filter(team=nov_team[0]).exists():
            continue

        BreakingTeam.objects.create(team=nov_team[0],
                                    seed=current_seed,
                                    effective_seed=current_seed,
                                    type_of_team=BreakingTeam.NOVICE)

        current_seed += 1

    if var_teams_to_break:
        pair(BreakingTeam.VARSITY)
    if nov_teams_to_break:
        pair(BreakingTeam.NOVICE)

    return True, "Success!"
