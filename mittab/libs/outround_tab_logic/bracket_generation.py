import math

def branch(seed, level, limit):
    level_sum = (2 ** level) + 1

    if limit == level + 1:
        return ((seed, level_sum - seed),)
    elif seed / 2 == 1:
        return branch(seed, level + 1, limit) + \
            branch(level_sum - seed, level + 1, limit)
    else:
        return branch(level_sum - seed, level + 1, limit) + \
            branch(seed, level + 1, limit)

def gen_bracket(num_teams):
    return branch(1, 1, math.log(num_teams, 2) + 1)
