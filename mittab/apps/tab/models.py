import string
import random

from django.db import models
from django.core.exceptions import ValidationError

class TabSettings(models.Model):
    key = models.CharField(max_length=20)
    value = models.IntegerField()
    def __unicode__(self):
        return "%s => %s" % (self.key,self.value)

    @classmethod
    def get(cls, key, default=None):
        try:
            return cls.objects.get(key=key).value
        except Exception as e:
            if default is None:
                raise e
            return default

    @classmethod
    def set(cls, key, value):
        try:
            obj = cls.objects.get(key=key)
            obj.value = value
            obj.save()
        except Exception as e:
            obj = cls.objects.create(key=key, value=value)


class School(models.Model):
    name = models.CharField(max_length=50, unique = True)
    def __unicode__(self):
        return self.name

    def delete(self):
        team_check = Team.objects.filter(school=self)
        judge_check = Judge.objects.filter(schools=self)
        if len(team_check) == 0 and len(judge_check) == 0:
            super(School, self).delete()
        else:
            raise Exception("School in use: [teams => %s,judges => %s]" % ([t.name for t in team_check], [j.name for j in judge_check]))


class Debater(models.Model):
    name = models.CharField(max_length=30, unique = True)
    #team_set is created by Team in the ManyToMany
    #team = models.ForeignKey('Team')
    #0 = Varsity, 1 = Novice
    VARSITY = 0
    NOVICE = 1
    NOVICE_CHOICES = (
        (VARSITY, u'Varsity'),
        (NOVICE, u'Novice'),
    )
    novice_status = models.IntegerField(choices=NOVICE_CHOICES)

    def __unicode__(self):
        return self.name

    def delete(self):
        teams = Team.objects.filter(debaters = self)
        if len(teams) == 0:
            super(Debater, self).delete()
        else :
            raise Exception("Debater on teams: %s" % ([t.name for t in teams]))

class Team(models.Model):
    name = models.CharField(max_length=30, unique = True)
    school = models.ForeignKey('School')
    hybrid_school = models.ForeignKey('School', blank=True, null=True,
            related_name='hybrid_school')
    debaters = models.ManyToManyField(Debater)
    # seed = 0 if unseeded, seed = 1 if free seed, seed = 2 if half seed, seed = 3 if full seed
    UNSEEDED = 0
    FREE_SEED = 1
    HALF_SEED = 2
    FULL_SEED = 3
    SEED_CHOICES= (
        (UNSEEDED, u'Unseeded'),
        (FREE_SEED, u'Free Seed'),
        (HALF_SEED, u'Half Seed'),
        (FULL_SEED, u'Full Seed'),
    )
    seed = models.IntegerField(choices=SEED_CHOICES)
    checked_in = models.BooleanField(default=True)

    def __unicode__(self):
        return self.name

    def delete(self):
        scratches = Scratch.objects.filter(team=self)
        for s in scratches:
            s.delete()
        super(Team, self).delete()


class Judge(models.Model):
    name = models.CharField(max_length=30, unique = True)
    rank = models.DecimalField(max_digits=4, decimal_places=2)
    schools = models.ManyToManyField(School)
    ballot_code = models.CharField(max_length=6, blank=True, null=True)

    def save(self, *args, **kwargs):
        # Generate a random ballot code for judges that don't have one
        if not self.ballot_code:
            choices = string.ascii_lowercase + string.digits
            code = ''.join(random.choice(choices) for _ in range(6))

            while Judge.objects.filter(ballot_code=code).first():
                code = ''.join(random.choice(choices) for _ in range(6))

            self.ballot_code = code

        super(Judge, self).save(*args, **kwargs)

    def __unicode__(self):
        return self.name

    def delete(self):
        checkins = CheckIn.objects.filter(judge=self)
        for c in checkins:
            c.delete()
        super(Judge, self).delete()

class Scratch(models.Model):
    judge = models.ForeignKey(Judge)
    team = models.ForeignKey(Team)
    TEAM_SCRATCH = 0
    TAB_SCRATCH = 1
    TYPE_CHOICES = (
        (TEAM_SCRATCH, u'Team Scratch'),
        (TAB_SCRATCH, u'Tab Scratch'),
    )
    scratch_type = models.IntegerField(choices=TYPE_CHOICES)

    def __unicode__(self):
        s_type = ("Team","Tab")[self.scratch_type]
        return u'{} <={}=> {}'.format(self.team, s_type, self.judge)


class Room(models.Model):
    name = models.CharField(max_length=30, unique=True)
    rank = models.DecimalField(max_digits=4, decimal_places=2)

    def __unicode__(self):
        return self.name

    def delete(self):
        rounds = Round.objects.filter(room=self)
        if len(rounds) == 0:
            super(Room, self).delete()
        else:
            raise Exception("Room is in round: %s" % ([str(r) for r in rounds]))


class Round(models.Model):
    round_number = models.IntegerField()
    gov_team = models.ForeignKey(Team, related_name="gov_team")
    opp_team = models.ForeignKey(Team, related_name="opp_team")
    chair = models.ForeignKey(Judge, null=True, blank=True, related_name="chair")
    judges = models.ManyToManyField(Judge,
                                    blank=True,
                                    related_name="judges")
    NONE = 0
    GOV = 1
    OPP = 2
    PULLUP_CHOICES = (
        (NONE, u'NONE'),
        (GOV, u'GOV'),
        (OPP, u'OPP'),
    )
    pullup = models.IntegerField(choices=PULLUP_CHOICES, default=0)
    UNKNOWN = 0
    GOV_VIA_FORFEIT = 3
    OPP_VIA_FORFEIT = 4
    ALL_DROP = 5
    ALL_WIN = 6
    VICTOR_CHOICES = (
        (UNKNOWN, u'UNKNOWN'),
        (GOV, u'GOV'),
        (OPP, u'OPP'),
        (GOV_VIA_FORFEIT, u'GOV via Forfeit'),
        (OPP_VIA_FORFEIT, u'OPP via Forfeit'),
        (ALL_DROP, u'ALL DROP'),
        (ALL_WIN, u'ALL WIN'),
    )
    room = models.ForeignKey(Room)
    victor = models.IntegerField(choices=VICTOR_CHOICES, default=0)

    def clean(self):
        if self.pk and self.chair not in self.judges.all():
            raise ValidationError("Chair must be a judge in the round")

    def __unicode__(self):
        return u'Round {} between {} and {}'.format(self.round_number, self.gov_team, self.opp_team)

    def delete(self):
        rounds = RoundStats.objects.filter(round=self)
        for rs in rounds:
            rs.delete()
        super(Round, self).delete()

class Bye(models.Model):
   bye_team = models.ForeignKey(Team)
   round_number = models.IntegerField()

   def __unicode__(self):
      return "Bye in round " + str(self.round_number) + " for " + str(self.bye_team)

class NoShow(models.Model):
   no_show_team = models.ForeignKey(Team)
   round_number = models.IntegerField()
   lenient_late = models.BooleanField(default=False)

   def __unicode__(self):
      return str(self.no_show_team) + " was no-show for round " + str(self.round_number)

class RoundStats(models.Model):
    debater = models.ForeignKey(Debater)
    round = models.ForeignKey(Round)
    # fewer digits?
    speaks = models.DecimalField(max_digits=6, decimal_places=4)
    ranks = models.DecimalField(max_digits=6, decimal_places=4)
    debater_role = models.CharField(max_length=4, null=True)

    def __unicode__(self):
        return "Results for %s in round %s" % (self.debater, self.round.round_number)

class CheckIn(models.Model):
    judge = models.ForeignKey(Judge)
    round_number = models.IntegerField()
    def __unicode__(self):
        return "Judge %s is checked in for round %s" % (self.judge, self.round_number)
