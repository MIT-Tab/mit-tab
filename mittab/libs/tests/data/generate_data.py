import mittab.libs.tab_logic as tab_logic
from mittab.apps.tab.models import Debater

import json

def generate_debater_rankings():
    scores = [(debater.name, debatertab_logic.debater_score(debater))
              for debater in Debater.objects.all()]
    with open("debater_scores.json", "w") as f:
        json.dump(scores, f)



if __name__ == "__main__":
    generate_debater_rankings()



