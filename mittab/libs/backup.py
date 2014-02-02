from mittab.apps.tab.models import *
from django.conf import settings
from django.core.servers.basehttp import FileWrapper

import shutil
import time
import os

def backup_round(dst_filename = None, round_number = None, btime = None):
    if round_number is None:
        round_number = TabSettings.objects.get(key="cur_round").value
    if btime is None:
        btime = int(time.time())
    print "Attempting to backup to backups directory"
    prefix = os.path.dirname(os.path.realpath(__file__))
    if dst_filename == None:
        dst_filename = prefix+"/backups/site_round_%i_%i.db" % (round_number, btime)
    else:
        dst_filename = prefix+"/backups/%s"%dst_filename
    src_filename = settings.DATABASES['default']['NAME']
    try:
        shutil.copy(src_filename, dst_filename)
        print "Copied %s to %s" % (src_filename, dst_filename)
    except:
        print "Could not copy %s to %s; most likely non-existant file"%(src_filename, dst_filename)

def handle_backup(f):
    prefix = os.path.dirname(os.path.realpath(__file__))
    dst_filename = prefix+"/backups/{}".format(f.name)
    print "Tried to write {}".format(dst_filename)
    try:
        with open(dst_filename, 'wb+') as destination:
            for chunk in f.chunks():
                destination.write(chunk)
    except Exception as e:
        print "Could not write {}".format(dst_filename)
        print "ERROR: {}".format(str(e))

def list_backups():
    print "Checking backups directory"
    prefix = os.path.dirname(os.path.realpath(__file__))
    return os.listdir(prefix+"/backups/")

def restore_from_backup(src_filename):
    print "Restoring from backups directory"
    prefix = os.path.dirname(os.path.realpath(__file__))
    src_filename = prefix + "/backups/%s" % src_filename
    dst_filename = settings.DATABASES['default']['NAME']
    try:
        shutil.copy(src_filename, dst_filename)
        print "Copied %s to %s" % (src_filename, dst_filename)
    except:
        print "Could not copy %s to %s; most likely non-existant file"%(src_filename, dst_filename)

#This does not work at all since the switch to AWS_KEYFILE
#TODO Clean this up
def restore_from_file(filename):
    print "Copying backup: %s => site.db" % filename
    src_filename = filename
    dst_filename = "site.db"
    try:
        shutil.copy(filename, dst_filename)
        print "Copied %s to %s" % (src_filename, dst_filename)
    except:
        print "Could not copy %s to %s; most likely non-existant file"%(src_filename, dst_filename)


def get_wrapped_file(src_filename):
    prefix = os.path.dirname(os.path.realpath(__file__))
    src_filename = prefix + "/backups/%s" % src_filename
    return FileWrapper(open(src_filename, "rb")), os.path.getsize(src_filename)


