import os
import shutil
import sys
import time

from optparse import make_option
from django.core.management import call_command
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from mittab.libs.backup import BACKUP_PREFIX
from mittab.apps.tab.models import TabSettings

class Command(BaseCommand):
    help = 'Setup a new tounament and backup the last one'

    def add_arguments(self, parser):
        parser.add_argument('--tab-password',
                dest='tab_password',
                help='Password for the tab user',
                nargs='?',
                default=User.objects.make_random_password(length=8))
        parser.add_argument('--entry-password',
                dest='entry_password',
                help='Password for the entry user',
                nargs='?',
                default=User.objects.make_random_password(length=8))
        parser.add_argument('backup_directory')

    def handle(self, *args, **options):
        if len(args) != 1:
            self.print_help('./manage.py', 'initialize_tourney')
            raise CommandError('Please supply valid arguments')

        backup_dir = args[0]
        path = BACKUP_PREFIX

        self.stdout.write("Proceeding to tournament creation")
        self.stdout.write("Creating directory for current tournament in backup directory")
        tournament_dir = os.path.join(backup_dir, str(int(time.time())))

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
            call_command("flush", interactive=False)
        except (IOError, os.error) as why:
            self.stdout.write("Failed to clear database")
            print(why)
            sys.exit(1)

        self.stdout.write("Creating tab/entry users")
        tab = User.objects.create_user("tab", None, options["tab_password"])
        tab.is_staff = True
        tab.is_admin = True
        tab.is_superuser = True
        tab.save()
        entry = User.objects.create_user("entry", None, options["entry_password"])

        self.stdout.write("Setting default tab settings")
        TabSettings.set("tot_rounds", 5)
        TabSettings.set("lenient_late", 0)
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

