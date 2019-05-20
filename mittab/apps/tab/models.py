import random

from haikunator import Haikunator
from django.db import models
from django.core.exceptions import ValidationError
from polymorphic.models import PolymorphicModel


class ModelWithTiebreaker(PolymorphicModel):
    tiebreaker = models.IntegerField(unique=True, null=True, blank=True)

    def save(self, *args, **kwargs):
        while not self.tiebreaker or \
                self.__class__.objects.filter(tiebreaker=self.tiebreaker).exists():
            self.tiebreaker = random.choice(range(0, 2 ** 16))

        super(ModelWithTiebreaker, self).save(*args, **kwargs)

    class Meta:
        abstract = True


class TabSettings(models.Model):
    key = models.CharField(max_length=20)
    value = models.IntegerField()

    class Meta:
        verbose_name_plural = "tab settings"

    def __str__(self):
        return "%s => %s" % (self.key, self.value)

    @classmethod
    def get(cls, key, default=None):
        if cls.objects.filter(key=key).exists():
            return cls.objects.get(key=key).value
        else:
            if default is None:
                raise ValueError("Invalid key '%s'" % key)
            return default

    @classmethod
    def set(cls, key, value):
        if cls.objects.filter(key=key).exists():
            obj = cls.objects.get(key=key)
            obj.value = value
            obj.save()
        else:
            obj = cls.objects.create(key=key, value=value)


class School(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name

    def delete(self, using=None, keep_parents=False):
        team_check = Team.objects.filter(school=self)
        judge_check = Judge.objects.filter(schools=self)
        if team_check.exists() or judge_check.exists():
            raise Exception(
                "School in use: [teams => %s,judges => %s]" %
                ([t.name for t in team_check], [j.name for j in judge_check]))
        else:
            super(School, self).delete(using, keep_parents)


class Debater(ModelWithTiebreaker):
    name = models.CharField(max_length=30, unique=True)
    VARSITY = 0
    NOVICE = 1
    NOVICE_CHOICES = (
        (VARSITY, "Varsity"),
        (NOVICE, "Novice"),
    )
    novice_status = models.IntegerField(choices=NOVICE_CHOICES)

    def __str__(self):
        return self.name

    def delete(self, using=None, keep_parents=False):
        teams = Team.objects.filter(debaters=self)
        if teams.exists():
            raise Exception("Debater on teams: %s" % ([t.name for t in teams]))
        else:
            super(Debater, self).delete(using, keep_parents)


class Team(ModelWithTiebreaker):
    name = models.CharField(max_length=30, unique=True)
    school = models.ForeignKey("School")
    hybrid_school = models.ForeignKey("School",
                                      blank=True,
                                      null=True,
                                      related_name="hybrid_school")
    debaters = models.ManyToManyField(Debater)
    UNSEEDED = 0
    FREE_SEED = 1
    HALF_SEED = 2
    FULL_SEED = 3
    SEED_CHOICES = (
        (UNSEEDED, "Unseeded"),
        (FREE_SEED, "Free Seed"),
        (HALF_SEED, "Half Seed"),
        (FULL_SEED, "Full Seed"),
    )
    seed = models.IntegerField(choices=SEED_CHOICES)
    checked_in = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    def delete(self, using=None, keep_parents=False):
        scratches = Scratch.objects.filter(team=self)
        for scratch in scratches:
            scratch.delete()
        super(Team, self).delete(using, keep_parents)


class Judge(models.Model):
    name = models.CharField(max_length=30, unique=True)
    rank = models.DecimalField(max_digits=4, decimal_places=2)
    schools = models.ManyToManyField(School)
    ballot_code = models.CharField(max_length=256,
                                   blank=True,
                                   null=True,
                                   unique=True)

    def save(self,
             force_insert=False,
             force_update=False,
             using=None,
             update_fields=None):
        # Generate a random ballot code for judges that don't have one
        if not self.ballot_code:
            haikunator = Haikunator()
            code = haikunator.haikunate(token_length=0)

            while Judge.objects.filter(ballot_code=code).first():
                code = haikunator.haikunate(token_length=0)

            self.ballot_code = code

        super(Judge, self).save(force_insert, force_update, using,
                                update_fields)

    def is_checked_in_for_round(self, round_number):
        return CheckIn.objects.filter(judge=self,
                                      round_number=round_number).exists()

    def __str__(self):
        return self.name

    def delete(self, using=None, keep_parents=False):
        checkins = CheckIn.objects.filter(judge=self)
        for checkin in checkins:
            checkin.delete()
        super(Judge, self).delete(using, keep_parents)


class Scratch(models.Model):
    judge = models.ForeignKey(Judge)
    team = models.ForeignKey(Team)
    TEAM_SCRATCH = 0
    TAB_SCRATCH = 1
    TYPE_CHOICES = (
        (TEAM_SCRATCH, "Team Scratch"),
        (TAB_SCRATCH, "Tab Scratch"),
    )
    scratch_type = models.IntegerField(choices=TYPE_CHOICES)

    class Meta:
        verbose_name_plural = "scratches"

    def __str__(self):
        s_type = ("Team", "Tab")[self.scratch_type]
        return "{} <={}=> {}".format(self.team, s_type, self.judge)


class Room(models.Model):
    name = models.CharField(max_length=30, unique=True)
    rank = models.DecimalField(max_digits=4, decimal_places=2)

    def __str__(self):
        return self.name

    def delete(self, using=None, keep_parents=False):
        rounds = Round.objects.filter(room=self)
        if rounds.exists():
            raise Exception("Room is in round: %s" % ([str(r)
                                                       for r in rounds]))
        else:
            super(Room, self).delete(using, keep_parents)


class Round(models.Model):
    round_number = models.IntegerField()
    gov_team = models.ForeignKey(Team, related_name="gov_team")
    opp_team = models.ForeignKey(Team, related_name="opp_team")
    chair = models.ForeignKey(Judge,
                              null=True,
                              blank=True,
                              related_name="chair")
    judges = models.ManyToManyField(Judge, blank=True, related_name="judges")
    NONE = 0
    GOV = 1
    OPP = 2
    PULLUP_CHOICES = (
        (NONE, "NONE"),
        (GOV, "GOV"),
        (OPP, "OPP"),
    )
    pullup = models.IntegerField(choices=PULLUP_CHOICES, default=0)
    UNKNOWN = 0
    GOV_VIA_FORFEIT = 3
    OPP_VIA_FORFEIT = 4
    ALL_DROP = 5
    ALL_WIN = 6
    VICTOR_CHOICES = (
        (UNKNOWN, "UNKNOWN"),
        (GOV, "GOV"),
        (OPP, "OPP"),
        (GOV_VIA_FORFEIT, "GOV via Forfeit"),
        (OPP_VIA_FORFEIT, "OPP via Forfeit"),
        (ALL_DROP, "ALL DROP"),
        (ALL_WIN, "ALL WIN"),
    )
    room = models.ForeignKey(Room)
    victor = models.IntegerField(choices=VICTOR_CHOICES, default=0)

    def clean(self):
        if self.pk and self.chair not in self.judges.all():
            raise ValidationError("Chair must be a judge in the round")

    def __str__(self):
        return "Round {} between {} and {}".format(self.round_number,
                                                   self.gov_team,
                                                   self.opp_team)

    def save(self,
             force_insert=False,
             force_update=False,
             using=None,
             update_fields=None):
        no_shows = NoShow.objects.filter(
            round_number=self.round_number,
            no_show_team__in=[self.gov_team, self.opp_team])

        if no_shows:
            no_shows.delete()

        super(Round, self).save(force_insert, force_update, using,
                                update_fields)

    def delete(self, using=None, keep_parents=False):
        rounds = RoundStats.objects.filter(round=self)
        for round_obj in rounds:
            round_obj.delete()
        super(Round, self).delete(using, keep_parents)


class Bye(models.Model):
    bye_team = models.ForeignKey(Team)
    round_number = models.IntegerField()

    def __str__(self):
        return "Bye in round " + str(self.round_number) + " for " + str(
            self.bye_team)


class NoShow(models.Model):
    no_show_team = models.ForeignKey(Team)
    round_number = models.IntegerField()
    lenient_late = models.BooleanField(default=False)

    def __str__(self):
        return str(self.no_show_team) + " was no-show for round " + str(
            self.round_number)


class RoundStats(models.Model):
    debater = models.ForeignKey(Debater)
    round = models.ForeignKey(Round)
    # fewer digits?
    speaks = models.DecimalField(max_digits=6, decimal_places=4)
    ranks = models.DecimalField(max_digits=6, decimal_places=4)
    debater_role = models.CharField(max_length=4, null=True)

    class Meta:
        verbose_name_plural = "round stats"

    def __str__(self):
        return "Results for %s in round %s" % (self.debater,
                                               self.round.round_number)


class CheckIn(models.Model):
    judge = models.ForeignKey(Judge)
    round_number = models.IntegerField()

    def __str__(self):
        return "Judge %s is checked in for round %s" % (self.judge,
                                                        self.round_number)
