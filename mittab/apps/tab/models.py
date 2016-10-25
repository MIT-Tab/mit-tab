import random
import string

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from localflavor.us.models import PhoneNumberField


class TabSettings(models.Model):
    """
    TabSettings is used to control the settings in the tabulation program. This is the object which is looked at. Look
    at pairing_views.start_new_tourney() to find the place where most of the TabSettings are initalised when a new
    tournament is started.
    """
    key = models.CharField(max_length=20)
    value = models.IntegerField()

    def __unicode__(self):
        return "%s => %s" % (self.key, self.value)

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
    """
    Contains information with regard to a school.
    """
    name = models.CharField(max_length=50, unique=True)
    """ name is the name of the school. """

    def __unicode__(self):
        return self.name

    def delete(self):
        team_check = Team.objects.filter(school=self)
        judge_check = Judge.objects.filter(schools=self)
        if len(team_check) == 0 and len(judge_check) == 0:
            super(School, self).delete()
        else:
            raise Exception("School in use: [teams => %s,judges => %s]" % (
                [t.name for t in team_check], [j.name for j in judge_check]))


class Debater(models.Model):
    """
    Contains information with regard to the debaters at the tournament.
    """
    # TODO it's honestly probably better to make a super-class containing the provider, phone, and name information.
    # Then, make the Judge and the Debater classes extend that class.
    name = models.CharField(max_length=30, unique=True)
    """ name is the name of the Debater """

    # team_set is created by Team in the ManyToMany
    # team = models.ForeignKey('Team')
    # 0 = Varsity, 1 = Novice
    VARSITY = 0
    NOVICE = 1
    NOVICE_CHOICES = (
        (VARSITY, u'Varsity'),
        (NOVICE, u'Novice'),
    )

    phone = PhoneNumberField(blank=True)
    """ field for the debater's phone number """

    provider = models.CharField(max_length=40, blank=True)
    """ field for the provider of the debater"""

    novice_status = models.IntegerField(choices=NOVICE_CHOICES)
    """ field for whether the debater is a novice or not """

    def __unicode__(self):
        return self.name

    def delete(self):
        teams = Team.objects.filter(debaters=self)
        if len(teams) == 0:
            super(Debater, self).delete()
        else:
            raise Exception("Debater on teams: %s" % ([t.name for t in teams]))


class Team(models.Model):
    """
    Contains information with regard to the team itself. Contains the team name, the team school, the debaters in that
    team, the team seed, and the information on whetehr that team is checked in or not.
    """
    alphanumeric = RegexValidator(r'^[0-9a-zA-Z ]*$', 'Only alphanumeric characters are allowed.')
    name = models.CharField(max_length=30, unique=True, validators=[alphanumeric])
    school = models.ForeignKey('School')
    debaters = models.ManyToManyField(Debater)

    # seed = 0 if unseeded, seed = 1 if free seed, seed = 2 if half seed, seed = 3 if full seed
    UNSEEDED = 0
    FREE_SEED = 1
    HALF_SEED = 2
    FULL_SEED = 3
    SEED_CHOICES = (
        (UNSEEDED, u'Unseeded'),
        (FREE_SEED, u'Free Seed'),
        (HALF_SEED, u'Half Seed'),
        (FULL_SEED, u'Full Seed'),
    )
    seed = models.IntegerField(choices=SEED_CHOICES)
    checked_in = models.BooleanField(default=True)

    def __unicode__(self):
        return self.name

    def delete(self, **kwargs):
        scratches = Scratch.objects.filter(team=self)
        for s in scratches:
            s.delete()
        super(Team, self).delete()


class Judge(models.Model):
    name = models.CharField(max_length=30, unique=True)
    """ name of the judge. cannot be more than 30 characters """

    rank = models.DecimalField(max_digits=4, decimal_places=2)
    """ rank of the judge, must have less than 4 digits and less than 2 decimal places """

    schools = models.ManyToManyField(School)
    """judge schools (takes many, because some dinos (ahem.) have like 4 or 5 affiliations for some reason...) """

    phone = PhoneNumberField(blank=True)
    """ judge phone number """

    provider = models.CharField(max_length=40, blank=True)
    """ provider for the judge """

    ballot_code = models.CharField(max_length=6, blank=True, null=True)
    """ ballot code for the judge for the e-ballots """

    def save(self, *args, **kwargs):
        if not self.ballot_code:
            choices = string.ascii_lowercase + string.digits
            code = ''.join(random.choice(choices) for _ in range(6))
            while Judge.objects.filter(ballot_code=code).first():
                # keep regenerating codes if it is something already selected
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
    """
    Keeps the data for scratches. Remember that scratches are private.
    """
    judge = models.ForeignKey(Judge)
    """ judge scratched """

    team = models.ForeignKey(Team)
    """ teams scratching """

    TEAM_SCRATCH = 0
    TAB_SCRATCH = 1
    TYPE_CHOICES = (
        (TEAM_SCRATCH, u'Team Scratch'),
        (TAB_SCRATCH, u'Tab Scratch'),
    )
    scratch_type = models.IntegerField(choices=TYPE_CHOICES)
    """ type of the scratch, i.e. tab or team """

    def __unicode__(self):
        """
        Returns string representation of this. This is the method which is called when MIT-TAB shows the people
        :return: Returns a string representation of the scratch.
        """
        s_type = ("Team", "Tab")[self.scratch_type]
        return str(self.team) + " <=" + str(s_type) + "=> " + str(self.judge)


class Room(models.Model):
    """
    Room object. Contains the name and the rank.
    """
    name = models.CharField(max_length=30, unique=True)
    """ name of the room to be displayed """

    rank = models.DecimalField(max_digits=4, decimal_places=2)
    """ rank of the room, from 99.99 to 0, limited to two decimal places. """

    def __unicode__(self):
        return self.name

    def delete(self):
        rounds = Round.objects.filter(room=self)
        if len(rounds) == 0:
            super(Room, self).delete()
        else:
            raise Exception("Room is in round: %s" % ([r.name for r in rounds]))


class Round(models.Model):
    round_number = models.IntegerField()
    gov_team = models.ForeignKey(Team, related_name="gov_team")
    opp_team = models.ForeignKey(Team, related_name="opp_team")
    chair = models.ForeignKey(Judge, null=True, blank=True, related_name="chair")
    judges = models.ManyToManyField(Judge,
                                    null=True,
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
        return "Round " + str(self.round_number) + " between " + str(self.gov_team) + " (GOV) and " + str(
            self.opp_team) + " (OPP)"

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
    lenient_late = models.BooleanField()

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


from south.modelsinspector import add_introspection_rules

add_introspection_rules([], ["^localflavor\.us\.models\.PhoneNumberField"])
