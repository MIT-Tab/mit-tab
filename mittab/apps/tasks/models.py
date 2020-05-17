from django.db import models, transaction


functions_by_key = {}

class Task(models.Model):
    QUEUED = 0
    RUNNING = 1
    COMPLETED = 2
    STATUS_CHOICES = (
        (QUEUED, 'Queued'),
        (RUNNING, 'Running'),
        (COMPLETED, 'Completed'),
    )

    key = models.CharField(max_length=20, unique=True)
    status = models.IntegerField(choices=STATUS_CHOICES)

    @classmethod
    def register(cls, key, function):
        if key not in functions_by_key:
            functions_by_key[key] = function
        else:
            raise ValueError('Key {} already registered!')

    def enqueue(cls, key):
        cls.objects.create(key=key, status=cls.QUEUED)

    def dequeue(cls):
        with transaction.atomic():
            to_dequeue = cls.objects.filter(status=cls.QUEUED).first()
            if to_dequeue:
                to_dequeue.status = cls.RUNNING
                to_dequeue.save()
            return to_dequeue
