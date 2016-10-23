import json
import os

base_dir = os.path.dirname(__file__)

def load_debater_rankings():
    return json.load(open(os.path.join(base_dir,
                                       'debater_finished_scores.json'), 'r'))

def load_team_rankings():
    return json.load(open(os.path.join(base_dir,
                                       'team_finished_scores.json'), 'r'))


