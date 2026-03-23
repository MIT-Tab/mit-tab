from django.apps import AppConfig


class TabConfig(AppConfig):
    default_auto_field = 'django.db.models.AutoField'
    name = 'mittab.apps.tab'
    verbose_name = 'MIT Tab'

    def ready(self):
        import mittab.apps.tab.signals  # noqa: F401
