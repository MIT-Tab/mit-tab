from django.db import models, transaction

from mittab.libs.errors import emit_current_exception


__functions = {}

class Task(models.Model):
    QUEUED = 0
    RUNNING = 1
    COMPLETED = 2
    FAILED = 3
    STATUS_CHOICES = (
        (QUEUED, "Queued"),
        (RUNNING, "Running"),
        (COMPLETED, "Completed"),
        (FAILED, "Failed"),
    )

    key = models.CharField(max_length=20, unique=True)
    status = models.IntegerField(choices=STATUS_CHOICES)
    error_message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def register(cls, key, function):
        if key not in __functions:
            __functions[key] = function
        else:
            raise ValueError("Key {} already registered!".format(key))

    @classmethod
    def enqueue(cls, key):
        if key not in __functions:
            raise ValueError("Key {} not registered!".format(key))
        with transaction.atomic():
            queued = cls.objects.exists(key=key, status=cls.QUEUED)
            running = cls.objects.exists(key=key, status=cls.RUNNING)
            if queued or running:
                return None
            else:
                return cls.objects.create(key=key, status=cls.QUEUED)

    @classmethod
    def dequeue(cls):
        with transaction.atomic():
            to_dequeue = cls.objects.filter(status=cls.QUEUED).first()
            if to_dequeue:
                to_dequeue.status = cls.RUNNING
                to_dequeue.save()
            return to_dequeue

    @classmethod
    def most_recent_run(cls, key):
        if key not in __functions:
            raise ValueError("Key {} not registered!".format(key))
        cls.objects.filter(key=key).latest('created_at')

    def execute(self):
        try:
            self.task.status = Task.RUNNING
            self.task.save()
            if self.key not in __functions:
                raise ValueError("Key {} not registed!".format(key))
            else:
                __functions[self.key]()
        except Exception as e:
            emit_current_exception()
            self.error_message = str(e)
            self.status = Task.FAILED
