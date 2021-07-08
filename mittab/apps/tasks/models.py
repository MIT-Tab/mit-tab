from django.db import models, transaction

from mittab.libs.errors import emit_current_exception


functions = {}

class Task(models.Model):
    QUEUED = 0
    RUNNING = 1
    COMPLETED = 2
    FAILED = 3
    DEQUEUED = 4
    STATUS_CHOICES = (
        (QUEUED, "Queued"),
        (RUNNING, "Running"),
        (COMPLETED, "Completed"),
        (FAILED, "Failed"),
        (DEQUEUED, "Dequeued"),
    )

    key = models.CharField(max_length=20, unique=True)
    status = models.IntegerField(choices=STATUS_CHOICES)
    error_message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def register(cls, key, function):
        if key not in functions:
            functions[key] = function
        else:
            raise ValueError("Key {} already registered!".format(key))

    @classmethod
    def enqueue(cls, key):
        if key not in functions:
            raise ValueError("Key {} not registered!".format(key))
        with transaction.atomic():
            statuses = [cls.QUEUED, cls.RUNNING]
            if cls.objects.filter(key=key, status__in=statuses).exists():
                return None
            else:
                return cls.objects.create(key=key, status=cls.QUEUED)

    @classmethod
    def dequeue(cls):
        with transaction.atomic():
            return cls.objects.filter(status__in=[cls.QUEUED, cls.FAILED]).first()
            if to_dequeue:
                to_dequeue.status = cls.DEQUEUED
                to_dequeue.save()
            return to_dequeue

    @classmethod
    def most_recent_run(cls, key):
        if key not in functions:
            raise ValueError("Key {} not registered!".format(key))
        cls.objects.filter(key=key).order_by('created_at').last()

    def is_terminated(self):
        return self.status in [Task.COMPLETED, Task.FAILED]

    def execute(self):
        try:
            self.status = Task.RUNNING
            self.save()
            if self.key not in functions:
                raise ValueError("Key {} not registed!".format(key))
            else:
                functions[self.key]()
        except Exception as e:
            import pdb; pdb.set_trace()
            emit_current_exception()
            self.error_message = str(e)
            self.status = Task.FAILED
            self.save()
