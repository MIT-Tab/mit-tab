from django.core.management.base import BaseCommand

from mittab.apps.tab.models import Round, TabSettings, RoundStats, Debater
from mittab.apps.tab.management.commands import utils


class Command(BaseCommand):

    def handle(self, *args, **options):
        cur_round = TabSettings.get("cur_round") - 1
        print(f"Simulating round {cur_round}...")
        rounds_to_simulate = Round.objects.filter(round_number=cur_round,
                                                  victor=Round.NONE)

        for round_obj in rounds_to_simulate:
            self.__simulate_round(round_obj)

    def __simulate_round(self, round_obj):
        results = utils.generate_random_results(round_obj)

        for position in ["pm", "mg", "lo", "mo"]:
            debater = Debater.objects.get(pk=results[position + "_debater"])
            stat = RoundStats(debater=debater,
                              round=round_obj,
                              speaks=results[position + "_speaks"],
                              ranks=results[position + "_ranks"],
                              debater_role=position)
            stat.save()
        round_obj.victor = results["winner"]
        round_obj.save()
