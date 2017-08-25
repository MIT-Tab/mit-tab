import mittab.libs.tab_logic as tab_logic
from mittab.apps.tab.models import Debater, Team

import json

def generate_debater_rankings(file_path="debater_scores.json"):
    scores = [(debater.name, tab_logic.debater_score(debater))
              for debater in Debater.objects.all()]
    with open(file_path, "w") as f:
        json.dump(scores, f)

def generate_team_rankings(file_path="team_scores.json"):
    scores = [(team.name, tab_logic.team_score(team))
              for team in Team.objects.all()]
    with open(file_path, "w") as f:
        json.dump(scores, f)

if __name__ == "__main__":
    generate_debater_rankings()



