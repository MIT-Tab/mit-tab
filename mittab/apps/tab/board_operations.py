"""Board bracket operations. No judge assignment, eligibility or scratch logic."""
from django.core.exceptions import ValidationError
from django.db import transaction

from mittab.apps.tab.auth_roles import is_apda_board_access_open, is_apda_board_user
from mittab.apps.tab.board_audit import (
    pairing_revision, pairing_snapshot, record_pairing_change,
)
from mittab.apps.tab.models import (
    AuditEvent, BreakingTeam, Outround, Room, RoomCheckIn, TabSettings,
)
from mittab.libs.cacheing import cache_logic
from mittab.libs.cacheing.public_cache import invalidate_all_public_caches
from mittab.libs.outround_tab_logic.bracket_generation import gen_bracket
from mittab.libs.outround_tab_logic.pairing import gov_team
from mittab.libs.tab_logic.rankings import get_team_rankings


def lock_board_tournament(actor):
    """Serialize board corrections and recheck access inside their transaction."""
    list(TabSettings.objects.select_for_update().filter(key="tot_rounds"))
    if not is_apda_board_user(actor) or not is_apda_board_access_open():
        raise ValidationError("Board access opens near the end of inrounds.")


def invalidate_results():
    cache_logic.clear_cache()
    invalidate_all_public_caches()


def bracket_problems(division):
    """Find stale descendants from public team/results state, never panel viability."""
    problems = []
    eliminated = set()
    rounds = Outround.objects.filter(
        type_of_round=division).order_by("-num_teams", "id")
    stages = sorted(set(rounds.values_list("num_teams", flat=True)), reverse=True)
    for stage in stages:
        seen = set()
        for pairing in rounds.filter(num_teams=stage):
            teams = {pairing.gov_team_id, pairing.opp_team_id}
            if len(teams) != 2 or seen & teams or eliminated & teams:
                problems.append(
                    f"Round of {stage} has duplicate or previously eliminated teams.")
            seen.update(teams)
        eliminated.update(pairing.loser.id for pairing in rounds.filter(num_teams=stage)
                          if pairing.loser)
    return list(dict.fromkeys(problems))


def recompute_seeds(division, actor):
    entries = {entry.team_id: entry for entry in BreakingTeam.objects.filter(
        type_of_team=division)}
    seeds = {team_id: entry.seed for team_id, entry in entries.items()}
    for pairing in Outround.objects.filter(
            type_of_round=division).order_by(
            "-num_teams", "id"):
        if pairing.winner and pairing.winner.id in seeds and pairing.loser.id in seeds:
            seeds[pairing.winner.id] = min(
                seeds[pairing.winner.id], seeds[pairing.loser.id])
            seeds[pairing.loser.id] = entries[pairing.loser.id].seed
    for team_id, entry in entries.items():
        if entry.effective_seed != seeds[team_id]:
            before = entry.effective_seed
            entry.effective_seed = seeds[team_id]
            entry.save(update_fields=["effective_seed"])
            AuditEvent.record(entry, AuditEvent.EDIT, actor, changes={
                "effective_seed": {"before": before, "after": entry.effective_seed},
            })


def _initialize_break(actor):
    """Create the initial team-only break, without deleting any existing bracket."""
    if BreakingTeam.objects.exists() or Outround.objects.exists():
        return
    varsity, novice = get_team_rankings(None)
    varsity_count = int(TabSettings.get("var_teams_to_break", 8))
    novice_count = int(TabSettings.get("nov_teams_to_break", 4))
    if varsity_count < 2 or novice_count < 0:
        raise ValidationError("Check the configured break sizes.")
    selected = [row[0]
                for row in varsity if row[0].break_preference == 0][:varsity_count]
    if len(selected) < 2:
        raise ValidationError("The varsity break needs at least two eligible teams.")
    selected_ids = {team.pk for team in selected}
    novice_selected = [row[0] for row in novice
                       if row[0].pk not in selected_ids][:novice_count]
    for division, teams in ((Outround.VARSITY, selected),
                            (Outround.NOVICE, novice_selected)):
        for seed, team in enumerate(teams, 1):
            entry = BreakingTeam.objects.create(
                team=team, seed=seed, effective_seed=seed, type_of_team=division)
            AuditEvent.record(
                entry,
                AuditEvent.CREATE,
                actor,
                changes={
                    "seed": seed,
                    "division": division,
                    "team": team.pk})


@transaction.atomic
def pair_board_outround(division, actor):
    lock_board_tournament(actor)
    if division not in {Outround.VARSITY, Outround.NOVICE}:
        raise ValidationError("Choose a valid division.")
    _initialize_break(actor)
    rounds = Outround.objects.filter(type_of_round=division)
    if bracket_problems(division):
        raise ValidationError("Correct the inconsistent bracket teams before pairing.")
    if rounds.filter(victor=Outround.UNKNOWN).exists():
        raise ValidationError(
            "Enter the existing outround outcomes before pairing the next round.")
    previous = rounds.order_by("num_teams").first()
    eliminated = {pairing.loser.id for pairing in rounds if pairing.loser}
    recompute_seeds(division, actor)
    entries = list(BreakingTeam.objects.filter(type_of_team=division)
                   .exclude(team_id__in=eliminated).select_related("team"))
    if len(entries) < 2:
        raise ValidationError("There are no more rounds to pair in this division.")
    if any(entry.effective_seed < 1 for entry in entries):
        raise ValidationError("Check the bracket seeds.")
    size = 2
    while size < len(entries):
        size *= 2
    if previous and size >= previous.num_teams:
        raise ValidationError("Check the previous round's teams and outcomes.")
    seeded = {entry.effective_seed: entry for entry in entries}
    if len(seeded) != len(entries):
        raise ValidationError("Check the bracket's effective seeds.")
    matchups = [(seeded[first], seeded[second]) for first, second in gen_bracket(size)
                if first in seeded and second in seeded]
    if not matchups:
        raise ValidationError("Check the bracket seeds; there are no valid matchups.")
    # Alphabetical room selection uses no judge requirements, ranking or eligibility.
    occupied = Outround.objects.filter(victor=Outround.UNKNOWN).exclude(room=None)
    rooms = list(Room.objects.filter(
        pk__in=RoomCheckIn.objects.filter(round_number=0).values("room_id"),
    ).exclude(pk__in=occupied.values("room_id")).order_by("name", "id"))
    if len(rooms) < len(matchups):
        raise ValidationError(
            "There are not enough unoccupied rooms checked in for outrounds.")
    for (first, second), room in zip(matchups, rooms):
        sidelock, gov = gov_team(first, second)
        opp = second if gov.pk == first.pk else first
        pairing = Outround.objects.create(
            type_of_round=division, num_teams=size, gov_team=gov.team,
            opp_team=opp.team, sidelock=sidelock, room=room,
        )
        record_pairing_change(pairing, None, actor, "board pairing")
    invalidate_results()
    return size


def _change_teams(pairing, data, actor):
    position = data["position"]
    team_id = data["team"]
    if not BreakingTeam.objects.filter(team_id=team_id,
                                       type_of_team=pairing.type_of_round).exists():
        raise ValidationError("Choose a breaking team from this division.")
    other_position = "opp_team_id" if position == "gov" else "gov_team_id"
    field = "gov_team_id" if position == "gov" else "opp_team_id"
    if team_id == getattr(pairing, other_position):
        raise ValidationError("Use Switch sides to swap these two teams.")
    old_id = getattr(pairing, field)
    if old_id == team_id:
        return
    peers = Outround.objects.select_for_update().filter(
        type_of_round=pairing.type_of_round, num_teams=pairing.num_teams,
    ).exclude(pk=pairing.pk)
    for peer in peers:
        for peer_field in ("gov_team_id", "opp_team_id"):
            if getattr(peer, peer_field) == team_id:
                before = pairing_snapshot(peer)
                setattr(peer, peer_field, old_id)
                peer.victor = Outround.UNKNOWN
                peer.save(update_fields=[peer_field[:-3], "victor"])
                record_pairing_change(peer, before, actor, "board team swap")
    setattr(pairing, field, team_id)
    pairing.victor = Outround.UNKNOWN


@transaction.atomic
def edit_board_outround(round_id, action, data, actor):
    lock_board_tournament(actor)
    pairing = Outround.objects.select_for_update().get(pk=round_id)
    if data.get("revision") != pairing_revision(pairing):
        raise ValidationError(
            "This round changed since you opened the page. "
            "Reload before correcting it.")
    before = pairing_snapshot(pairing)
    if action == "result":
        pairing.victor = data["victor"]
    elif action == "sides":
        pairing.gov_team_id, pairing.opp_team_id = (
            pairing.opp_team_id, pairing.gov_team_id)
        pairing.victor = {1: 2, 2: 1, 3: 4, 4: 3}.get(pairing.victor, pairing.victor)
        pairing.choice = {1: 2, 2: 1}.get(pairing.choice, pairing.choice)
    elif action == "team":
        _change_teams(pairing, data, actor)
    elif action == "room":
        room_id = data["room"]
        if room_id and not Room.objects.filter(pk=room_id).exists():
            raise ValidationError("Choose a valid room.")
        occupied = Outround.objects.filter(room_id=room_id, victor=Outround.UNKNOWN)
        if (room_id and pairing.victor == Outround.UNKNOWN
                and occupied.exclude(pk=pairing.pk).exists()):
            raise ValidationError(
                "That room is already assigned to an undecided outround.")
        pairing.room_id = room_id
    elif action == "choice":
        pairing.choice = data["choice"]
    else:
        raise ValidationError("Choose a valid correction.")
    pairing.save(update_fields=["gov_team", "opp_team", "victor", "choice", "room"])
    record_pairing_change(pairing, before, actor, "board correction")
    recompute_seeds(pairing.type_of_round, actor)
    invalidate_results()
    return pairing
