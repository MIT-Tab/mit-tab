import math
import random

from mittab.apps.tab.models import *

from mittab.libs.outround_tab_logic.checks import have_enough_rooms
from mittab.libs.outround_tab_logic.bracket_generation import gen_bracket
from mittab.libs.outround_tab_logic.helpers import offset_to_quotient
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


def _setting_key_for_current_challenger(type_of_round, num_teams):
    return f"challenge_current_{type_of_round}_{num_teams}"


def _setting_key_for_challenge_once(type_of_round):
    return f"challenge_once_applied_{type_of_round}"


def _get_challenge_settings():
    return {
        "mode": int(TabSettings.get("outround_pairing_mode", OutroundPairingMode.STANDARD)),
        "seed_basis": int(TabSettings.get(
            "challenge_seed_basis",
            ChallengeSeedBasis.PREVIOUS_ROUND,
        )),
    }


def _ordered_breaking_teams(base_queryset, seed_basis):
    order_field = "seed" if seed_basis == ChallengeSeedBasis.ORIGINAL else "effective_seed"
    return list(base_queryset.select_related("team").order_by(order_field, "team__id"))


def challenge_round_enabled(type_of_break, num_teams, actual_num_teams):
    settings = _get_challenge_settings()
    mode = settings["mode"]
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


def _resolve_current_challenger(remaining_teams, current_override_id):
    remaining_ids = {entry.team.id for entry in remaining_teams}
    if current_override_id in remaining_ids:
        return next(entry for entry in remaining_teams if entry.team.id == current_override_id)
    return remaining_teams[0]


def _get_challenge_pairs(type_of_break, num_teams, base_queryset, settings):
    seed_basis = settings["seed_basis"]
    if settings["mode"] == OutroundPairingMode.CHALLENGE_ONCE:
        seed_basis = ChallengeSeedBasis.PREVIOUS_ROUND

    ordered = _ordered_breaking_teams(base_queryset, seed_basis)
    remaining = ordered[:]
    picks = {
        entry.challenger_id: entry.challenged_id
        for entry in OutroundChallenge.objects.filter(
            type_of_round=type_of_break,
            num_teams=num_teams,
            challenger_id__in=[b.team.id for b in ordered],
        )
    }
    current_override = int(TabSettings.get(
        _setting_key_for_current_challenger(type_of_break, num_teams), 0
    ) or 0)

    result = []
    seed_updates = []
    while remaining:
        challenger = _resolve_current_challenger(remaining, current_override)
        current_override = 0
        remaining_ids = {entry.team.id for entry in remaining if entry.team.id != challenger.team.id}
        challenged_id = picks.get(challenger.team.id)
        if challenged_id not in remaining_ids:
            # Default challenge policy when no challenge was explicitly entered:
            # challenge the currently lowest-ranked available seed.
            challenged = remaining[-1]
        else:
            challenged = next(entry for entry in remaining if entry.team.id == challenged_id)

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
    settings = _get_challenge_settings()
    mode = settings["mode"]
    enabled = mode != OutroundPairingMode.STANDARD and _is_power_of_two(num_teams) \
        and num_teams >= 4
    if mode == OutroundPairingMode.CHALLENGE_ONCE and TabSettings.get(
        _setting_key_for_challenge_once(type_of_break), 0
    ):
        enabled = False
    if not enabled:
        return {"enabled": False}

    lost_outround = [t.loser.id for t in Outround.objects.all() if t.loser]
    base_queryset = BreakingTeam.objects.filter(
        type_of_team=type_of_break
    ).exclude(
        team__id__in=lost_outround
    )
    if base_queryset.count() != num_teams:
        return {"enabled": False}

    seed_basis = settings["seed_basis"]
    if mode == OutroundPairingMode.CHALLENGE_ONCE:
        seed_basis = ChallengeSeedBasis.PREVIOUS_ROUND

    ordered = _ordered_breaking_teams(base_queryset, seed_basis)
    challenge_map = {
        entry.challenger_id: entry.challenged_id
        for entry in OutroundChallenge.objects.filter(
            type_of_round=type_of_break,
            num_teams=num_teams,
            challenger_id__in=[b.team.id for b in ordered],
        )
    }
    current_challenger_id = int(TabSettings.get(
        _setting_key_for_current_challenger(type_of_break, num_teams), 0
    ) or 0)
    used_targets = set(challenge_map.values())
    pending = [b for b in ordered if b.team.id not in challenge_map and b.team.id not in used_targets]

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
        "team_options": [
            {
                "id": b.team.id,
                "label": f"[{i + 1}] {b.team.name}",
            }
            for i, b in enumerate(ordered)
        ],
        "challenges": [
            {
                "challenger_id": b.team.id,
                "challenger_name": b.team.name,
                "challenged_id": challenge_map.get(b.team.id),
            }
            for b in ordered
        ],
        "current_challenger_id": current_challenger_id,
        "pending_ids": [b.team.id for b in pending],
    }


def save_challenge_pick(type_of_break, num_teams, challenger_id, challenged_id):
    if challenger_id == challenged_id:
        raise ValueError("A team cannot challenge itself.")

    challenge_context = get_challenge_setup_context(type_of_break, num_teams)
    if not challenge_context.get("enabled"):
        raise ValueError("Challenge entries are not available for this round.")

    valid_team_ids = {option["id"] for option in challenge_context["team_options"]}
    if challenger_id not in valid_team_ids or challenged_id not in valid_team_ids:
        raise ValueError("Selected teams are not in this challenge pool.")

    conflicting = OutroundChallenge.objects.filter(
        type_of_round=type_of_break,
        num_teams=num_teams,
        challenged_id=challenged_id,
    ).exclude(challenger_id=challenger_id).exists()
    if conflicting:
        raise ValueError("That team has already been challenged.")

    OutroundChallenge.objects.update_or_create(
        type_of_round=type_of_break,
        num_teams=num_teams,
        challenger_id=challenger_id,
        defaults={"challenged_id": challenged_id},
    )


def save_current_challenger(type_of_break, num_teams, challenger_id):
    challenge_context = get_challenge_setup_context(type_of_break, num_teams)
    if not challenge_context.get("enabled"):
        raise ValueError("Challenge entries are not available for this round.")

    valid_team_ids = {option["id"] for option in challenge_context["team_options"]}
    if challenger_id and challenger_id not in valid_team_ids:
        raise ValueError("Selected current challenger is invalid.")
    TabSettings.set(_setting_key_for_current_challenger(type_of_break, num_teams), challenger_id or 0)


def perform_the_break():
    teams, nov_teams = cache_logic.cache_fxn_key(
        get_team_rankings,
        "team_rankings",
        cache_logic.DEFAULT,
        None
    )

    nov_teams_to_break = TabSettings.get("nov_teams_to_break")
    var_teams_to_break = TabSettings.get("var_teams_to_break")

    if not nov_teams_to_break or not var_teams_to_break:
        return False, "Please check your break tab settings"

    # This forces a refresh of the breaking teams
    Outround.objects.all().delete()
    OutroundChallenge.objects.all().delete()
    BreakingTeam.objects.all().delete()
    TabSettings.set(_setting_key_for_challenge_once(BreakingTeam.VARSITY), 0)
    TabSettings.set(_setting_key_for_challenge_once(BreakingTeam.NOVICE), 0)

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

    pair(BreakingTeam.VARSITY)
    pair(BreakingTeam.NOVICE)

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


def get_next_available_room(num_teams, type_of_break):
    base_queryset = Outround.objects.filter(num_teams=num_teams,
                                            type_of_round=type_of_break)

    var_to_nov = TabSettings.get("var_to_nov", 2)

    var_to_nov = offset_to_quotient(var_to_nov)

    other_queryset = Outround.objects.filter(type_of_round=not type_of_break)

    if type_of_break == BreakingTeam.VARSITY:
        other_queryset = other_queryset.filter(num_teams=num_teams / var_to_nov)
    else:
        other_queryset = other_queryset.filter(num_teams=num_teams * var_to_nov)

    rooms = [r.room
             for r in RoomCheckIn.objects.filter(round_number=0)
             .prefetch_related("room")]
    rooms.sort(key=lambda r: r.rank, reverse=True)

    for room in rooms:
        if not base_queryset.filter(room=room).exists() and \
           not other_queryset.filter(room=room).exists():
            return room
    return None


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
    lost_outround = [t.loser.id for t in Outround.objects.all() if t.loser]

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
            room=get_next_available_room(num_teams,
                                         type_of_break=type_of_break),
            sidelock=sidelock
        )

    if challenge_settings["mode"] == OutroundPairingMode.CHALLENGE_ONCE and challenge_pairs:
        TabSettings.set(_setting_key_for_challenge_once(type_of_break), 1)
