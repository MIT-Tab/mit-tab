from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from mittab.libs.backup import get_backup_prefix

import os
import shutil
import sys

class Command(BaseCommand):
    help = 'Setup a new tounament and backup the last one'

    def add_arguments(self, parser):
        parser.add_argument('tournament_name', nargs='+', type=str)
        parser.add_argument('backup_directory', nargs='+', type=str)
        parser.add_argument('--tab-password', dest='tab_password',
                help='Password for the tab user')
        parser.add_argument('--entry-password', dest='entry_password',
                help='Password for the tab user')

    def handle(self, *args, **options):
        if not (options.get('tournament_name') and options.get('backup_directory')):
            self.print_help('./manage.py', 'initialize_tourney')
            raise CommandError('Please supply valid arguments')

        tournament_name = options['tournament_name'][0]
        backup_dir = options['backup_directory'][0]
        path = get_backup_prefix()

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
           print why
           sys.exit(1)

        self.stdout.write("Copying blank db to pairing_db.sqlite3")
        try:
            shutil.copy(path + "/pairing_db_clean.sqlite3", path + "/pairing_db.sqlite3")
        except (IOError, os.error) as why:
            self.stdout.write("Failed to copy clean database to pairing_db.sqlite3")
            print why
            sys.exit(1)

        self.stdout.write("Setting passwords for tab and entry")
        tab = User.objects.get(username="tab")
        entry = User.objects.get(username="entry")

        tab.set_password(options['tab_password'])
        entry.set_password(options['entry_password'])
        tab.save()
        entry.save()

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

