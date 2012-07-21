from tab.models import *
from django.conf import settings
import shutil
import time

#AWS related stuff, mostly just a namespace so we don't allow unintential access to this
class AWSBackup:
    awskeyfile = settings.AWS_KEYFILE
    key,secret = None,None
    try:
        from boto.s3.connection import S3Connection       
        from boto.s3.key import Key as Key
        with open(awskeyfile) as f:
            key = f.readline().strip()
            secret = f.readline().strip()
        conn = S3Connection(key,secret)
    except Exception as e:
        print "Caught exception while setting up AWS: ", e
        print "[ERROR] Could not set up AWS connection.  Perhaps boto is not installed or you do not have the AWS_KEYFILE setting."
        key, secret = None, None
    if key and secret:
        use_aws = True
    else:
        use_aws = False

def backup_round(dst_filename = None, round_number = TabSettings.objects.get(key="cur_round").value):
    if AWSBackup.use_aws:
        try:
            print "Attempting to backup the database to AWS"
            bucket = AWSBackup.conn.get_bucket('mit-tab-backups')
            k = AWSBackup.Key(bucket)
            dbase_fh = settings.DATABASES['default']['NAME']
            if dst_filename:
                k.key = '%i-sitedb-%s' % (int(time.time()), dst_filename)
            else:
                k.key = '%i-sitedb-backup' % int(time.time())
            k.set_contents_from_filename(dbase_fh)
            print "DONE backing up database to AWS"
        except Exception as e:
            print "FAILED to backup to AWS"
            print e
            AWSBackup.use_aws = False
            backup_round(dst_filename, round_number)
    else:
        print "Cannot backup to AWS, attempting to backup to backups directory"
        return
        if dst_filename == None:
            dst_filename = "backups/site_%i.db" % int(time.time())
        else:
            dst_filename = "backups/%s"%dst_filename
        src_filename = settings.DATABASES['default']['NAME']
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



