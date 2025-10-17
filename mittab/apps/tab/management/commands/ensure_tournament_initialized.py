from django.core.management import BaseCommand, CommandError, call_command
from django.db import connection


class Command(BaseCommand):
    help = "Ensure the tournament has been initialized and the flag table exists."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tab-password",
            dest="tab_password",
            help="Password for the tab user; required when initializing.",
        )
        parser.add_argument(
            "--entry-password",
            dest="entry_password",
            help="Optional password for the entry user when initializing.",
        )

    def handle(self, *args, **options):
        tab_password = options.get("tab_password")
        entry_password = options.get("entry_password")

        if not tab_password:
            raise CommandError("--tab-password is required to initialize the tournament.")

        if self._tournament_exists():
            self.stdout.write("Tournament already initialized, skipping setup.")
            return

        self.stdout.write("Initializing tournament data")
        initialize_kwargs = {
            "tab_password": tab_password,
            "first_init": True,
        }
        if entry_password:
            initialize_kwargs["entry_password"] = entry_password

        call_command("initialize_tourney", **initialize_kwargs)

        self.stdout.write("Creating tournament_initialized flag table")
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS tournament_initialized("
                "id int not null, PRIMARY KEY (id))"
            )

    def _tournament_exists(self):
        with connection.cursor() as cursor:
            cursor.execute("SHOW TABLES LIKE 'tournament_initialized'")
            return cursor.fetchone() is not None
