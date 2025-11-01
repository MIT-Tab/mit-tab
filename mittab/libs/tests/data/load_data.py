import json
import os

BASE_DIR = os.path.dirname(__file__)


def load_debater_rankings():
    file_path = os.path.join(BASE_DIR, "debater_finished_scores.json")
    with open(file_path, "r", encoding="utf-8") as json_file:
        return json.load(json_file)


def load_team_rankings():
    file_path = os.path.join(BASE_DIR, "team_finished_scores.json")
    with open(file_path, "r", encoding="utf-8") as json_file:
        return json.load(json_file)
