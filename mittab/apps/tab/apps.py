from django.apps import AppConfig

from mittab.apps.tasks.models import Task
from mittab.libs.tab_logic import do_pairing


class TabAppConfig(AppConfig):
    name = "tab"
    verbose_name = "MIT-Tab"

    def ready(self):
        Task.register("pair_round", do_pairing)
