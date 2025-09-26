import json

from mittab.apps.tab.models import BreakingTeam, Outround
from mittab.libs.cache_logic import cache
from mittab.libs.outround_tab_logic.bracket_generation import gen_bracket

LOCKED, WAITING, READY, COMPLETED = 0, 1, 2, 4
WINNER_SIDES = {
    Outround.GOV: 0,
    Outround.GOV_VIA_FORFEIT: 0,
    Outround.OPP: 1,
    Outround.OPP_VIA_FORFEIT: 1,
}

def _build_match_skeleton(bracket_size):
    # Create the first round with initial pairings from bracket generation
    rounds = [[
        {"slots": [{"seeds": {left}, "team": None, "participant": None, "bye": False},
                   {"seeds": {right}, "team": None, "participant": None, "bye": False}],
         "winner_side": None, "outround": None}
        for left, right in gen_bracket(bracket_size)
    ]]
    # Build next rounds by pairing adjacent matches from previous round
    while len(rounds[-1]) > 1:
        def make_match(left, right):
            # Combine seed sets from both child matches
            left_seeds = left["slots"][0]["seeds"] | left["slots"][1]["seeds"]
            right_seeds = right["slots"][0]["seeds"] | right["slots"][1]["seeds"]
            return {"slots": [{"seeds": left_seeds, "team": None,
                               "participant": None, "bye": False},
                              {"seeds": right_seeds, "team": None,
                               "participant": None, "bye": False}],
                    "winner_side": None, "outround": None}

        # Pair up adjacent matches (even/odd indices) to create the next round
        rounds.append([make_match(left, right)
                       for left, right in zip(rounds[-1][::2], rounds[-1][1::2])])
    return rounds

def _opponent_payload(slot, match, slot_index, round_index):
    if slot["bye"]:
        return None
    participant = slot["participant"]
    if not participant:
        return {"id": None} if round_index else None
    payload = {"id": participant}
    if match["winner_side"] is not None:
        payload["result"] = "win" if match["winner_side"] == slot_index else "loss"
    return payload

def _match_status(match):
    left, right = match["slots"]
    if left["bye"] or right["bye"] or (not left["team"] and not right["team"]):
        return LOCKED
    if not left["team"] or not right["team"]:
        return WAITING
    return COMPLETED if match["winner_side"] is not None else READY



def _serialize_bracket(rounds):
    rounds_payload = [
        {"id": idx, "number": idx, "stage_id": 1,
         "group_id": 1}
        for idx in range(1, len(rounds) + 1)
    ]
    matches_payload, metadata = [], {}
    match_id = 1
    for round_index, round_matches in enumerate(rounds):
        for match_index, match in enumerate(round_matches, 1):
            opponents = [
                _opponent_payload(slot, match, slot_idx, round_index)
                for slot_idx, slot in enumerate(match["slots"])
            ]
            matches_payload.append({
                "id": match_id,
                "number": match_index,
                "stage_id": 1, "group_id": 1, "round_id": round_index + 1,
                "child_count": 0,
                "status": _match_status(match),
                "opponent1": opponents[0],
                "opponent2": opponents[1],
            })
            matchup = match.get("outround")
            if matchup:
                room = matchup.room
                judges_rel = matchup.judges
                chair = matchup.chair
                judges = ([{"name": str(j.name), "is_chair": j == chair}
                           for j in judges_rel.all()] if judges_rel else [])
                slots = match["slots"]

                def get_fallback_display(slot):
                    seeds_display = " / ".join(f"Seed {seed}"
                                               for seed in sorted(slot["seeds"]))
                    return {"display": seeds_display or "TBD", "debaters": ""}

                metadata[match_id] = {
                    "id": matchup.id,
                    "room": str(room.name) if room else "TBD",
                    "judges": judges,
                    "gov_team": slots[0].get("display_info",
                                             get_fallback_display(slots[0])),
                    "opp_team": slots[1].get("display_info",
                                             get_fallback_display(slots[1])),
                }
            match_id += 1
    return rounds_payload, matches_payload, metadata

def _build_bracket_payload(bracket_size, seed_to_team, outround_pairings):
    rounds = _build_match_skeleton(bracket_size)
    if not rounds:
        return [], [], {}, []
    participants, team_to_participant, participants_by_team_id = [], {}, {}
    team_to_seed = {team.id: seed for seed, team in seed_to_team.items() if team}

    def ensure_participant(team, seed):
        existing = team_to_participant.get(team.id)
        if existing:
            return existing
        participant_id = len(participants) + 1
        display = f"[{seed}] {team}"
        members = f"[{seed}] {team.debaters_display()}"
        participant_data = {
            "id": participant_id,
            "tournament_id": 1,
            "name": display, "team_name": display,
            "debaters_names": members,
        }
        participants.append(participant_data)
        team_to_participant[team.id] = participant_id
        participants_by_team_id[team.id] = participant_data
        return participant_id

    def fill_slot(slot, team, seed):
        slot["team"] = team
        slot["participant"] = ensure_participant(team, seed)
        slot["bye"] = False
        participant = participants_by_team_id[team.id]
        slot["display_info"] = {
            "display": participant["name"],
            "debaters": participant["debaters_names"]
        }

    for match in rounds[0]:
        for slot in match["slots"]:
            seed = next(iter(slot["seeds"]))
            team = seed_to_team.get(seed)
            slot["bye"] = not team
            if team:
                fill_slot(slot, team, seed)

    for block in outround_pairings:
        for matchup in block["rounds"]:
            if matchup.num_teams < 2:
                continue
            # Calculate which round this matchup belongs to based on number of teams
            # bit_length gives log2 + 1, subtract 1 for round offset from end
            round_index = len(rounds) - (matchup.num_teams.bit_length() - 1)
            if not 0 <= round_index < len(rounds):
                continue
            teams = [matchup.gov_team, matchup.opp_team]
            if None in teams:
                continue
            seeds = [team_to_seed.get(team.id) for team in teams]
            # Find bracket match corresponding to this matchup by checking seeds
            # by checking if the teams' seeds are in the expected positions
            match, invert = None, False
            for candidate_match in rounds[round_index]:
                left, right = candidate_match["slots"]
                if seeds[0] in left["seeds"] and seeds[1] in right["seeds"]:
                    match, invert = candidate_match, False
                    break
                if seeds[0] in right["seeds"] and seeds[1] in left["seeds"]:
                    # Teams are swapped from expected positions, so we'll need to invert
                    match, invert = candidate_match, True
                    break
            if None in seeds or not match:
                continue
            slots = match["slots"]
            if invert:
                slots, teams, seeds = slots[::-1], teams[::-1], seeds[::-1]
            for slot, team, seed in zip(slots, teams, seeds):
                fill_slot(slot, team, seed)
            match["outround"] = matchup
            side = WINNER_SIDES.get(matchup.victor, None)
            # Apply XOR to flip winner side if teams were inverted
            match["winner_side"] = side ^ invert if side is not None else None

    # Propagate wins up the bracket tree
    last_round = len(rounds) - 1
    for round_index, round_matches in enumerate(rounds):
        for match_index, match in enumerate(round_matches):
            slots = match["slots"]
            winner_side = match["winner_side"]
            # Auto-advance teams that have byes (opponent didn't show up)
            if winner_side is None:
                winner_side = 0 if slots[0]["team"] and slots[1]["bye"] else (
                    1 if slots[1]["team"] and slots[0]["bye"] else None
                )
            match["winner_side"] = winner_side
            if round_index == last_round or winner_side is None:
                continue
            # Calculate parent slot in next round: match_index // 2 gives parent match,
            # match_index % 2 gives which slot (0 or 1) within that parent match
            parent = rounds[round_index + 1][match_index // 2]["slots"][match_index % 2]
            team = slots[winner_side].get("team")
            if team:
                fill_slot(parent, team, team_to_seed.get(team.id))

    rounds_payload, matches_payload, metadata = _serialize_bracket(rounds)
    return rounds_payload, matches_payload, metadata, participants

def _calculate_bracket_size(outround_pairings):
    matchups = [m for b in outround_pairings for m in b["rounds"] if m.num_teams >= 2]
    if not matchups:
        return None, None
    size = max(m.num_teams for m in matchups)
    # Ensure bracket size is a power of 2 by rounding up to next power of 2
    # Uses bit manipulation: size & (size - 1) == 0 tests if size is already power of 2
    if size & (size - 1):
        size = 1 << (size - 1).bit_length()
    return size, matchups[0].type_of_round

def _get_seed_to_team_mapping(bracket_size, type_of_round):
    breaking = BreakingTeam.objects.filter(seed__lte=bracket_size)
    if type_of_round is not None:
        breaking = breaking.filter(type_of_team=type_of_round)
    return {entry.seed: entry.team for entry in
            breaking.select_related("team") if entry.team}

def generate_bracket_data(outround_pairings):
    bracket_size, type_of_round = _calculate_bracket_size(outround_pairings)
    if bracket_size is None:
        return None
    seed_to_team = _get_seed_to_team_mapping(bracket_size, type_of_round)
    rounds_payload, matches_payload, metadata, participants = _build_bracket_payload(
        bracket_size, seed_to_team, outround_pairings
    )
    stage = {
        "id": 1,
        "tournament_id": 1,
        "name": "Elimination Bracket",
        "type": "single_elimination",
        "number": 1,
        "settings": {"size": bracket_size},
    }
    return {
        "stages": [stage],
        "groups": [{"id": 1, "stage_id": 1, "number": 1}],
        "rounds": rounds_payload,
        "participants": participants,
        "matches": matches_payload,
        "matchGames": [],
        "match_metadata": metadata,
    }

@cache(60)
def get_bracket_data_json(outround_pairings):
    try:
        bracket_data = generate_bracket_data(outround_pairings)
        return json.dumps(bracket_data) if bracket_data is not None else json.dumps({})
    except Exception:
        return json.dumps({})
