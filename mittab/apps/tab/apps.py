from django.apps import AppConfig

class TabAppConfig(AppConfig):
    name = "mittab.apps.tab"
    verbose_name = "MIT-Tab"

    def ready(self):
        from mittab.apps.tasks.models import Task
        from mittab.libs.tab_logic import do_pairing
        Task.register("pair_round", do_pairing)
        return super().ready()
