#!/usr/bin/python
import os, sys
path=os.path.dirname(os.path.realpath(__file__))
sys.path.append(path)
os.environ['DJANGO_SETTINGS_MODULE'] = 'settings'
from tab.models import *
from django.contrib.auth.models import User
from argparse import ArgumentParser
import shutil, errno


parser = ArgumentParser(description="Setup a new tounament and backup the last one")
parser.add_argument("-n", "--name", dest="tournament_name", required=True,
                  help="Name of the *old* tournament to save")
parser.add_argument("-b", "--backup-dir", dest="backup_dir", required=True,
                  help="Directory to copy current tournament state to")
parser.add_argument("-tp", "--tab-password", dest="tab_password", required=False,
                  help="Password for the tab user")
parser.add_argument("-ep", "--entry-password", dest="entry_password", required=False,
                  help="Password for the entry user")

args= parser.parse_args()

if args.tab_password is None:
  print "No password provided for tab, generating password"
  args.tab_password = User.objects.make_random_password(length=8)

if args.entry_password is None:
  print "No password provided for entry, generating password"
  args.entry_password = User.objects.make_random_password(length=8)

print "Proceeding to tournament creation"
tournament_dir = args.backup_dir + "/" + args.tournament_name
print "Creating directory for current tournament in backup directory"
if not os.path.exists(tournament_dir):
      os.makedirs(tournament_dir)
print "Copying current tournament state to backup tournament directory: ", tournament_dir
try:
  shutil.copy(path+"/site.db", tournament_dir)
  shutil.rmtree(tournament_dir+"/backups", ignore_errors=True)
  shutil.copytree(path+"/backups", tournament_dir + "/backups")
except (IOError, os.error) as why:
  print "Failed to backup current tournament state"
  print why
  sys.exit(1)

print "Copying blank db to site.db"
try:
  shutil.copy(path + "/site-clean.db", path + "/site.db")
except (IOError, os.error) as why:
  print "Failed to copy clean database to site.db"
  print why
  sys.exit(1)

print "Setting passwords for tab and entry"
tab = User.objects.get(username="tab")
entry = User.objects.get(username="entry")

tab.set_password(args.tab_password)
entry.set_password(args.entry_password)
tab.save()
entry.save()

print "Done setting up tournament, after backing up old one. New tournament information:"
print "%s | %s" % ("Username".ljust(10," "), "Password".ljust(10, " "))
print "%s | %s" % ("tab".ljust(10," "), args.tab_password.ljust(10, " "))
print "%s | %s" % ("entry".ljust(10," "), args.entry_password.ljust(10, " "))

