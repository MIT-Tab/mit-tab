"""Collection of useful methods for manipulating pairing data"""

from mittab.apps.tab.models import Round, RoundStats
import random

# Speaks every quarter point
speak_range = [23 + 0.25 * i for i in range(17)]


def generate_speaks_for_debater(debater, is_forfeit=False):
    """
    Generates a fake speak for a debater chosen from a normal distribution
    centered on a speak value determined by the hash of the debaters name. This
    ensures that each debater speaks differently, but they tend to speak the
    same as themselves.

    Arguments:
    debater (Debater model) -- A debater who has a name
    is_forfeit (boolean) -- Whether a forfeit occurred

    Returns:
    speaks (int) -- A number in speak_range (23-27) that the debater spoke,
                    unless there was a forfeit in which case 0.0
    """
    if is_forfeit:
        return 0.0

    debater_average = hash(debater.name) % len(speak_range)
    sampled_speak = int(random.gauss(debater_average, 2))
    # Limit to 0 -> len(speak_range) - 1
    sampled_speak = max(min(sampled_speak, len(speak_range) - 1), 0)
    return speak_range[sampled_speak]


def generate_result_for_round(round_obj, prob_forfeit=0.0, prob_ironman=0.0):
    """
    Generates a round result and saves it in the database

    Arguments:
    round_obj (Round model) -- A round to generate results for
    prob_forfeit (float) -- The probability that a forfeit occurs
    prob_ironman (float) -- The probability that a iron man occurs for either
                            team

    Returns:
    (round_obj, round_stat1, .. round_stat4) -- A tuple containing database
                                                models ready to be saved
    """
    is_forfeit = random.random() < prob_forfeit
    is_ironman = False
    if not is_forfeit:
        is_ironman = random.random() < prob_ironman

    # Determine ironmen and ordering of debaters which determines roles
    gov_team, opp_team = round_obj.gov_team, round_obj.opp_team
    gov_debaters = gov_team.debaters.all()
    opp_debaters = opp_team.debaters.all()
    if is_ironman:
        # FIXME: refactor so we can have two ironmen
        if random.choice([True, False]):
            gov_debaters = [random.choice(gov_debaters)] * 2
        else:
            opp_debaters = [random.choice(opp_debaters)] * 2

    # Generate speak values using generate_speaks_for_debater
    gov_speaks = [
        (debater, generate_speaks_for_debater(debater, is_forfeit))
        for debater in gov_debaters
    ]
    opp_speaks = [
        (debater, generate_speaks_for_debater(debater, is_forfeit))
        for debater in opp_debaters
    ]
    all_speaks = gov_speaks + opp_speaks

    # Generate ranks based on the speak values, but first shuffle so that ties
    # are broken arbitrarily during ranks calculations
    random.shuffle(all_speaks)
    all_speaks.sort(key=lambda x: x[1], reverse=True)
    all_points = [(d[0], (d[1], i + 1)) for i, d in enumerate(all_speaks)]

    # Decide who won based on speaks and ranks
    gov_points, opp_points = [0, 0], [0, 0]
    for debater, points in all_points:
        if debater in gov_debaters:
            gov_points[0] += points[0]
            gov_points[1] -= points[1]
        else:
            opp_points[0] += points[0]
            opp_points[1] -= points[1]

    if gov_points > opp_points and not is_forfeit:
        # Gov won
        round_obj.victor = Round.GOV
    elif opp_points > gov_points and not is_forfeit:
        # Opp won
        round_obj.victor = Round.OPP
    elif not is_forfeit:
        # Tie
        round_obj.victor = random.choice((Round.GOV, Round.OPP))
    else:
        # Forfeit
        round_obj.victor = random.choice(
            (Round.GOV_VIA_FORFEIT, Round.OPP_VIA_FORFEIT, Round.ALL_DROP)
        )

    # Generate RoundStats, shuffle for role randomization
    random.shuffle(all_points)
    gov_roles, opp_roles = ["pm", "mg"], ["lo", "mo"]
    round_stats = []
    for debater, points in all_points:
        role = ""
        if debater in gov_debaters:
            role = gov_roles.pop()
        else:
            role = opp_roles.pop()
        stats = RoundStats(
            debater=debater,
            round=round_obj,
            speaks=points[0],
            ranks=points[1],
            debater_role=role,
        )
        round_stats.append(stats)

    return tuple([round_obj] + round_stats)


def generate_results(round_number, prob_forfeit=0.0, prob_ironman=0.0, seed="BEEF"):
    """Generates results for the existing round"""
    random.seed(seed)
    for round_obj in Round.objects.filter(round_number=round_number):
        results = generate_result_for_round(
            round_obj, prob_forfeit=prob_forfeit, prob_ironman=prob_ironman
        )
        for result in results:
            result.save()
