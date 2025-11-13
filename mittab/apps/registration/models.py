from haikunator import Haikunator
from django.db import models
from mittab.apps.tab.models import School


class RegistrationConfig(models.Model):
    allow_new_registrations = models.BooleanField(default=True)
    allow_registration_edits = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get_active(cls):
        return cls.objects.first()

    @classmethod
    def get_or_create_active(cls):
        config = cls.get_active()
        if config:
            return config
        return cls.objects.create()

    def can_create(self):
        return self.allow_new_registrations

    def can_modify(self):
        return self.allow_registration_edits


class RegistrationContent(models.Model):
    description = models.TextField(blank=True)
    completion_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get_solo(cls):
        instance = cls.objects.first()
        if instance:
            return instance
        return cls.objects.create()

    def __str__(self):
        return "Registration Content"


class Registration(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE)
    email = models.EmailField()
    herokunator_code = models.CharField(max_length=255, unique=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.herokunator_code:
            haikunator = Haikunator()
            code = haikunator.haikunate(token_length=0)
            while Registration.objects.filter(herokunator_code=code).exists():
                code = haikunator.haikunate(token_length=0)
            self.herokunator_code = code
        super().save(*args, **kwargs)
