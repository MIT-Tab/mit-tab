import mittab.libs.tab_logic as tab_logic
from mittab.apps.tab.models import Debater, Team

import json

def generate_debater_rankings():
    scores = [(debater.name, tab_logic.debater_score(debater))
              for debater in Debater.objects.all()]
    with open("debater_finished_scores.json", "w") as f:
        json.dump(scores, f)

def generate_team_rankings():
    scores = [(team.name, tab_logic.team_score(team))
              for team in Team.objects.all()]
    with open("team_finished_scores.json", "w") as f:
        json.dump(scores, f)


if __name__ == "__main__":
    generate_debater_rankings()
    generate_team_rankings()
