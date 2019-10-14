import math


# Recursive function that generated bracket

def branch(seed, level, limit):
    # Level is how deep in the recursion basically
    # Limit is the depth of the recursion to get to 1, ie, for 8 teams, this value would be 4 (dividing by 2)
    level_sum = (2 ** level) + 1

    # How many teams there are at the current spot
    # Seed is where you are currently, think of it as a branch, you could be at the 1 seed, or the 5th branch, it's a tree.

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
