from mittab.apps.tab.models import TabSettings, BreakingTeam


def _normalize_bracket_size(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 0

    if value <= 0:
        return 0

    return 1 << (value - 1).bit_length()


def _stage_sizes(start_size):
    size = _normalize_bracket_size(start_size)
    if size == 0:
        return []

    if size <= 2:
        return [size]

    stages = []
    while True:
        stages.append(size)
        size = size // 2
        if size <= 2:
            stages.append(max(1, size))
            break

    return stages


def _concurrency_pairs():
    novice_start = _normalize_bracket_size(
        TabSettings.get("nov_teams_to_break", 0)
    )

    if novice_start == 0:
        return []

    varsity_start_raw = (
        TabSettings.get("novice_outrounds_start_at", novice_start)
    )
    varsity_start = _normalize_bracket_size(varsity_start_raw)

    novice_stages = _stage_sizes(novice_start)
    varsity_stages = _stage_sizes(varsity_start)

    max_len = max(len(novice_stages), len(varsity_stages))
    pairs = []

    for idx in range(max_len):
        novice_value = novice_stages[idx] if idx < len(novice_stages) else 0
        varsity_value = varsity_stages[idx] if idx < len(varsity_stages) else 0
        if novice_value == 0 and varsity_value == 0:
            break
        pairs.append((novice_value, varsity_value))

    return pairs


def get_concurrent_round_size(num_teams, type_of_round):
    bracket_size = _normalize_bracket_size(num_teams)
    if bracket_size == 0:
        return 0

    for novice_size, varsity_size in _concurrency_pairs():
        if type_of_round == BreakingTeam.NOVICE and novice_size == bracket_size:
            return varsity_size
        if type_of_round == BreakingTeam.VARSITY and varsity_size == bracket_size:
            return novice_size

    return 0
