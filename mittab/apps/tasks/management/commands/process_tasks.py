import time

from django.core.management.base import BaseCommand

from mittab.apps.models import Task

class Command(BaseCommand):
    help = "Process any queued tasks"

    while True:
        cur_task = Task.dequeue()
        if cur_task:
            cur_task.execute()
        else:
            print("No task ready to execute, sleeping")
            time.sleep(1)
