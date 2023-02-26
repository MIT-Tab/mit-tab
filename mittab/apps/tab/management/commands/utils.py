import random

from mittab.apps.tab.models import Round


SPEAKS_RANGE = list(range(15, 35))


def generate_random_results(round_obj, ballot_code=None):
    winner = random.choice([Round.GOV, Round.OPP])
    speaks = sorted([random.choice(SPEAKS_RANGE) for _ in range(4)])

    winning_team = round_obj.gov_team if winner == Round.GOV else round_obj.opp_team
    losing_team = round_obj.opp_team if winner == Round.GOV else round_obj.gov_team

    if winner == Round.GOV:
        losing_positions = ["lo", "mo"]
        winning_positions = ["pm", "mg"]
    else:
        losing_positions = ["pm", "mg"]
        winning_positions = ["lo", "mo"]

    debaters_rank_order = [
        winning_team.debaters.first(),
        winning_team.debaters.last(),
        losing_team.debaters.first(),
        losing_team.debaters.last(),
    ]

    form_data = {
        "round_instance": round_obj.id,
        "winner": winner
    }
    if ballot_code is not None:
        form_data["ballot_code"] = ballot_code

    for rank in range(1, 5):
        debater = debaters_rank_order[rank - 1]
        speak = speaks.pop()
        if rank <= 2:
            position = winning_positions.pop()
        else:
            position = losing_positions.pop()

        form_data[position + "_ranks"] = rank
        form_data[position + "_speaks"] = speak
        form_data[position + "_debater"] = debater.id
    return form_data
