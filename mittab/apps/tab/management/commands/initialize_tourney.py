import os
import shutil
import sys

from optparse import make_option
from django.core.management import execute_from_command_line
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from mittab.libs.backup import BACKUP_PREFIX

class Command(BaseCommand):
    args = '<tournament_name> <backup_directory>'
    help = 'Setup a new tounament and backup the last one'
    option_list = BaseCommand.option_list + (
            make_option("--tab-password", dest="tab_password",
                help="Password for the tab user"),
            make_option("--entry-password", dest="entry_password",
                help="Password for the entry user"))

    def handle(self, *args, **options):
        if len(args) != 2:
            self.print_help('./manage.py', 'initialize_tourney')
            raise CommandError('Please supply valid arguments')

        tournament_name, backup_dir = args
        path = BACKUP_PREFIX

        for user in ['tab', 'entry']:
            option_name = '%s_password' % user
            if options[option_name] is None or options[option_name].strip() == '':
                self.stdout.write("No password provided for %s, generating password" % user)
                options[option_name] = User.objects.make_random_password(length=8)

        self.stdout.write("Proceeding to tournament creation")
        self.stdout.write("Creating directory for current tournament in backup directory")
        tournament_dir = os.path.join(backup_dir, tournament_name)

        if not os.path.exists(tournament_dir):
            os.makedirs(tournament_dir)

        if not os.path.exists(path + "/backups"):
            os.makedirs(path + "/backups")

        self.stdout.write("Copying current tournament state to backup tournament directory: %s" % tournament_dir)
        try:
            shutil.copy(path + "/pairing_db.sqlite3", tournament_dir)
            shutil.rmtree(tournament_dir + "/backups", ignore_errors=True)
            shutil.copytree(path + "/backups", tournament_dir + "/backups")
        except (IOError, os.error) as why:
           self.stdout.write("Failed to backup current tournament state")
           print(why)
           sys.exit(1)

        self.stdout.write("Clearing data from database")
        try:
            execute_from_command_line(["manage.py", "flush"])
        except (IOError, os.error) as why:
            self.stdout.write("Failed to clear database")
            print(why)
            sys.exit(1)

        self.stdout.write("Creating tab/entry users")
        tab = User(username="tab")
        tab.save()

        entry = User(username="entry")
        entry.save()

        tab.set_password(options["tab_password"])
        entry.set_password(options["entry_password"])
        tab.save()
        entry.save()

        self.stdout.write("Setting default tab settings")
        TabSettings.set("tot_rounds", 5)
        TabSettings.set("lenient_late", 0)
        TabSettings.set("nov_teams_to_break", 4)
        TabSettings.set("var_teams_to_break", 8)
        TabSettings.set("cur_round", 1)

        self.stdout.write("Cleaning up old backups")
        try:
            shutil.rmtree(path + "/backups")
            os.makedirs(path + "/backups")
        except (IOError, os.error) as why:
            self.stdout.write("Failed to copy clean database to pairing_db.sqlite3")
            print why
            sys.exit(1)

        self.stdout.write("Done setting up tournament, after backing up old one. New tournament information:")
        self.stdout.write("%s | %s" % ("Username".ljust(10," "), "Password".ljust(10, " ")))
        self.stdout.write("%s | %s" % ("tab".ljust(10," "), options['tab_password'].ljust(10, " ")))
        self.stdout.write("%s | %s" % ("entry".ljust(10," "), options['entry_password'].ljust(10, " ")))

