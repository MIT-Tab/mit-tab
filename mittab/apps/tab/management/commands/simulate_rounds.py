import random

from django.core.management.base import BaseCommand

from mittab.apps.tab.models import Round, TabSettings, RoundStats


class Command(BaseCommand):
    SPEAKS_RANGE = list(range(15, 35))

    def handle(self, *args, **options):
        cur_round = TabSettings.get("cur_round") - 1
        print(("Simulating round %s..." % cur_round))
        rounds_to_simulate = Round.objects.filter(round_number=cur_round,
                                                  victor=Round.NONE)

        for round_obj in rounds_to_simulate:
            self.__simulate_round(round_obj)

    def __simulate_round(self, round_obj):
        winner = random.choice([Round.GOV, Round.OPP])
        speaks = sorted([random.choice(self.SPEAKS_RANGE) for _ in range(4)])

        winning_team = round_obj.gov_team if winner == Round.GOV else round_obj.opp_team
        winning_positions = ["pm", "mg"
                             ] if winner == Round.GOV else ["lo", "mo"]

        losing_team = round_obj.opp_team if winner == Round.GOV else round_obj.gov_team
        losing_positions = ["lo", "mo"
                            ] if winner == Round.GOV else ["pm", "mg"]

        debaters_rank_order = [
            winning_team.debaters.first(),
            winning_team.debaters.last(),
            losing_team.debaters.first(),
            losing_team.debaters.last(),
        ]

        for rank in range(1, 5):
            debater = debaters_rank_order[rank - 1]
            speak = speaks.pop()
            position = winning_positions.pop(
            ) if rank <= 2 else losing_positions.pop()

            stat = RoundStats(debater=debater,
                              round=round_obj,
                              speaks=speak,
                              ranks=rank,
                              debater_role=position)
            stat.save()
        round_obj.victor = winner
        round_obj.save()
