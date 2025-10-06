from haikunator import Haikunator
from django.db import models
from django.utils import timezone

from mittab.apps.tab.models import School, Team, Judge, Debater


class RegistrationConfig(models.Model):
    registration_open = models.DateField(null=True, blank=True)
    registration_close = models.DateField(null=True, blank=True)
    tournament_start = models.DateField(null=True, blank=True)
    extra_information = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get_active(cls):
        return cls.objects.first()

    def is_open(self):
        today = timezone.now().date()
        if self.registration_open and today < self.registration_open:
            return False
        if self.registration_close and today > self.registration_close:
            return False
        return True


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


class RegistrationTeam(models.Model):
    registration = models.ForeignKey(Registration,
                                     related_name="teams",
                                     on_delete=models.CASCADE)
    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    is_free_seed = models.BooleanField(default=False)


class RegistrationJudge(models.Model):
    registration = models.ForeignKey(Registration,
                                     related_name="judges",
                                     on_delete=models.CASCADE)
    judge = models.ForeignKey(Judge, on_delete=models.CASCADE)


class RegistrationTeamMember(models.Model):
    registration_team = models.ForeignKey(RegistrationTeam,
                                          related_name="members",
                                          on_delete=models.CASCADE)
    debater = models.ForeignKey(Debater, on_delete=models.CASCADE)
    school = models.ForeignKey(School, on_delete=models.CASCADE)
    position = models.IntegerField(default=0)
