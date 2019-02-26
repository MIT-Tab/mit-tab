import random

from django.core.management.base import BaseCommand

from mittab.apps.tab.models import Round, TabSettings, RoundStats

class Command(BaseCommand):
    SPEAKS_RANGE = range(15, 35)

    def handle(self, *args, **options):
        cur_round = TabSettings.get("cur_round") - 1
        print("Simulating round %s..." % cur_round)
        rounds_to_simulate = Round.objects.filter(round_number=cur_round, victor=Round.NONE)

        for r in rounds_to_simulate:
            print(r)
            self.__simulate_round(r)


    def __simulate_round(self, r):
        winner = random.choice([Round.GOV, Round.OPP])
        speaks = sorted([random.choice(self.SPEAKS_RANGE) for _ in range(4)])

        winning_team = r.gov_team if winner == Round.GOV else r.opp_team
        winning_positions = ["pm", "mg"] if winner == Round.GOV else ["lo", "mo"]

        losing_team = r.opp_team if winner == Round.GOV else r.gov_team
        losing_positions = ["lo", "mo"] if winner == Round.GOV else ["pm", "mg"]

        debaters_rank_order = [
            winning_team.debaters.first(),
            winning_team.debaters.last(),
            losing_team.debaters.first(),
            losing_team.debaters.last(),
        ]

        for rank in range(1, 5):
            debater = debaters_rank_order[rank - 1]
            speak = speaks.pop()
            position = winning_positions.pop() if rank <= 2 else losing_positions.pop()

            stat = RoundStats(
                    debater=debater,
                    round=r,
                    speaks=speak,
                    ranks=rank,
                    debater_role=position
                )
            stat.save()
        r.victor = winner
        r.save()
