"""Collection of useful methods for manipulating pairing data"""

from mittab.apps.tab.models import Round
import random

# Speaks every quarter point
speak_range = [23 + .25 * i for i in range(17)]

def generate_speaks_for_debater(debater):
    """
    Generates a fake speak for a debater chosen from a normal distribution
    centered on a speak value determined by the hash of the debaters name. This
    ensures that each debater speaks differently, but they tend to speak the
    same as themselves.

    Arguments:
    debater (Debater model) -- A debater who has a name

    Returns:
    speaks (int) -- A number in speak_range (23-27) that the debater spoke
    """
    debater_average = hash(debater.name) % len(speak_range)
    sampled_speak = int(random.gauss(debater_average, 2))
    # Limit to 0 -> len(speak_range)
    sampled_speak = max(min(sampled_speak, len(speak_range) - 1), 0)
    return speak_range[sampled_speak]

def generate_result_for_round(round_obj):
    """
    Generates a round result and saves it in the database

    Arguments:
    round_obj (Round model) -- A round to generate results for
    """
    gov_team, opp_team = round_obj.gov_team, round_obj.opp_team
    gov_debaters = gov_team.debaters.all()
    opp_debaters = opp_team.debaters.all()

    # Generate speak values using generate_speaks_for_debater
    gov_speaks = [(d, generate_speaks_for_debater(d)) for d in gov_debaters]
    opp_speaks = [(d, generate_speaks_for_debater(d)) for d in gov_debaters]
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

    return gov_points, opp_points


def generate_results(round_number, prob_forfeit=0.0,
                     prob_ironman=0.0, seed='BEEF'):
    """Generates results for the existing round"""
    random.seed(seed)
    pass
