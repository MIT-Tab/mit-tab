import os, sys
sys.path.append('/var/www/tab/mittab')
os.environ['DJANGO_SETTINGS_MODULE'] = 'settings'
import django.core.handlers.wsgi
application = django.core.handlers.wsgi.WSGIHandler()

print >> sys.stderr, sys.path
