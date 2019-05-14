import json
import os

BASE_DIR = os.path.dirname(__file__)


def load_debater_rankings():
    return json.load(open(os.path.join(BASE_DIR, "debater_finished_scores.json"), "r"))


def load_team_rankings():
    return json.load(open(os.path.join(BASE_DIR, "team_finished_scores.json"), "r"))
