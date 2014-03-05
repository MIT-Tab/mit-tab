"""Collection of useful methods for manipulating pairing data"""

from mittab.apps.tab.models import Round
import random

# Speaks every quarter point
speak_range = [23 + .25 * i for i in range(17)]

def generate_speaks_for_debater(debater):
    """Generates a fake speak for a debater chosen from a normal distribution
    centered on a speak value determined by the hash of the debaters name. This
    ensures that each debater speaks differently, but they tend to speak
    the same as themselves."""
    debater_average = hash(debater.name) % len(speak_range)
    speaks = speak_range[int(random.gauss(debater_average, 2))]

    # Account for outliers
    min_speaks, max_speaks = min(speak_range), max(speak_range)
    if speaks < min_speaks:
        speaks = min_speaks
    elif speaks > max_speaks:
        speaks = max_speaks

    return speaks

def generate_results(round_number, prob_forfeit=0.0,
                     prob_ironman=0.0, seed='BEEF'):
    """Generates results for the existing round"""
    pass
