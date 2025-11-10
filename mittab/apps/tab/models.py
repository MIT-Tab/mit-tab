import random

from haikunator import Haikunator
from django.db import models
from django.core.exceptions import ValidationError

from mittab.libs.cacheing import cache_logic


class TabSettings(models.Model):
    key = models.CharField(max_length=50)
    value = models.IntegerField(null=True, blank=True)
    value_string = models.CharField(max_length=200, null=True, blank=True)

    class Meta:
        verbose_name_plural = "tab settings"

    def __str__(self):
        display_value = (self.value_string if self.value_string is not None
                         else self.value)
        return f"{self.key} => {display_value}"

    @classmethod
    def get(cls, key, default=None):
        def safe_get():
            setting = cls.objects.filter(key=key).first()
            if setting is not None:
                return (setting.value_string if setting.value_string
                        is not None else setting.value)
            return None

        result = cache_logic.cache_fxn_key(
            safe_get,
            f"tab_settings_{key}",
            cache_logic.PERSISTENT,
        )
        if result is None and default is None:
            raise ValueError(f"No TabSetting with key '{key}'")
        elif result is None:
            return default
        else:
            return result

    @classmethod
    def set(cls, key, value):
        if isinstance(value, str):
            value_string = value
            value_num = None
        else:
            value_num = value
            value_string = None

        if cls.objects.filter(key=key).exists():
            obj = cls.objects.get(key=key)
            obj.value = value_num
            obj.value_string = value_string
            obj.save()
        else:
            obj = cls.objects.create(key=key, value=value_num,
                                     value_string=value_string)

    def delete(self, using=None, keep_parents=False):
        cache_logic.invalidate_cache(f"tab_settings_{self.key}",
                                     cache_logic.PERSISTENT)
        super(TabSettings, self).delete(using, keep_parents)

    def save(self,
             force_insert=False,
             force_update=False,
             using=None,
             update_fields=None):
        cache_logic.invalidate_cache(f"tab_settings_{self.key}",
                                     cache_logic.PERSISTENT)
        super(TabSettings, self).save(force_insert, force_update, using, update_fields)


class School(models.Model):
    name = models.CharField(max_length=50, unique=True)
    apda_id = models.IntegerField(blank=True, null=True, default=-1)

    def __str__(self):
        return self.name

    def delete(self, using=None, keep_parents=False):
        team_check = Team.objects.filter(school=self)
        judge_check = Judge.objects.filter(schools=self)
        if team_check.exists() or judge_check.exists():
            teams = [t.name for t in team_check]
            judges = [j.name for j in judge_check]
            message = f"School in use: [teams => {teams},judges => {judges}]"
            raise ValidationError(message)
        super(School, self).delete(using, keep_parents)

    @property
    def display(self):
        schools_public = not TabSettings.get("use_team_codes", 0)

        if schools_public:
            return self.name
        return ""

    class Meta:
        ordering = ["name"]


class Debater(models.Model):
    name = models.CharField(max_length=30, unique=True)
    VARSITY = 0
    NOVICE = 1
    NOVICE_CHOICES = (
        (VARSITY, "Varsity"),
        (NOVICE, "Novice"),
    )
    novice_status = models.IntegerField(choices=NOVICE_CHOICES)
    qualified = models.BooleanField(default=False)
    tiebreaker = models.IntegerField(unique=True, null=True, blank=True)
    apda_id = models.IntegerField(blank=True, null=True, default=-1)

    def save(self,
             force_insert=False,
             force_update=False,
             using=None,
             update_fields=None):
        while not self.tiebreaker or \
                Debater.objects.filter(tiebreaker=self.tiebreaker).exists():
            self.tiebreaker = random.choice(range(0, 2**16))
        super(Debater, self).save(force_insert, force_update, using, update_fields)

    @property
    def num_teams(self):
        return self.team_set.count()

    @property
    def display(self):
        return self.name

    def __str__(self):
        return self.name

    def team(self):
        return self.team_set.first()

    def delete(self, using=None, keep_parents=False):
        teams = Team.objects.filter(debaters=self)
        if teams.exists():
            raise ValidationError(f"Debater on teams: {[t.name for t in teams]}")
        super(Debater, self).delete(using, keep_parents)

    class Meta:
        ordering = ["name"]


class Team(models.Model):
    name = models.CharField(max_length=30, unique=True)
    school = models.ForeignKey("School", on_delete=models.CASCADE)
    hybrid_school = models.ForeignKey("School",
                                      blank=True,
                                      null=True,
                                      on_delete=models.SET_NULL,
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
    team_code = models.CharField(max_length=255,
                                 blank=True,
                                 null=True,
                                 unique=True)

    VARSITY = 0
    NOVICE = 1
    BREAK_PREFERENCE_CHOICES = (
        (VARSITY, "Varsity"),
        (NOVICE, "Novice")
    )

    required_room_tags = models.ManyToManyField("RoomTag", blank=True)
    break_preference = models.IntegerField(default=0,
                                           choices=BREAK_PREFERENCE_CHOICES)
    tiebreaker = models.IntegerField(unique=True, null=True, blank=True)
    ranking_public = models.BooleanField(default=True)

    @classmethod
    def with_preloaded_relations_for_tab_card(cls):
        return cls.objects.prefetch_related(
            "gov_team",
            "opp_team",
            "gov_team__judges",
            "opp_team__judges",
            "gov_team__opp_team",
            "opp_team__gov_team",
            "debaters",
            "debaters__roundstats_set",
            "debaters__roundstats_set__round",
            "debaters__team_set",
            "debaters__team_set__no_shows",
        )

    @classmethod
    def with_preloaded_relations_for_tabbing(cls):
        return cls.objects.prefetch_related(
            "gov_team",  # poorly named relation, gets rounds as gov team
            "opp_team",  # poorly named relation, rounds as opp team
            "gov_team_outround",  # outround data for gov team
            "opp_team_outround",  # outround data for opp team
            # for all gov rounds, load the opp team's gov+opp rounds (opp-strength)
            # and team record
            "gov_team__opp_team__gov_team",
            "gov_team__opp_team__opp_team",
            "gov_team__opp_team__byes",
            "gov_team__opp_team",
            # for all opp rounds, load the gov team's gov+opp rounds (opp-strength)
            # and team record
            "opp_team__gov_team__gov_team",
            "opp_team__gov_team__opp_team",
            "opp_team__gov_team__byes",
            "opp_team__gov_team",
            # basic stats/metadata
            "byes",
            "no_shows",
            "debaters",
            # individual's stats/metadata
            "debaters__roundstats_set",
            "debaters__roundstats_set__round",
            "debaters__team_set",
            "debaters__team_set__no_shows",
        )

    def set_unique_team_code(self):
        haikunator = Haikunator()

        def gen_haiku_and_clean():
            code = haikunator.haikunate(token_length=0).replace("-", " ").title()

            return code

        code = gen_haiku_and_clean()

        while Team.objects.filter(team_code=code).first():
            code = gen_haiku_and_clean()

        self.team_code = code

    def save(self,
             force_insert=False,
             force_update=False,
             using=None,
             update_fields=None):
        # Generate a team code for teams that don't have one
        if not self.team_code:
            self.set_unique_team_code()

        while not self.tiebreaker or \
                Team.objects.filter(tiebreaker=self.tiebreaker).exists():
            self.tiebreaker = random.choice(range(0, 2**16))

        super(Team, self).save(force_insert, force_update, using, update_fields)

    @property
    def display_backend(self):
        use_team_codes_backend = TabSettings.get("team_codes_backend", 0)

        if use_team_codes_backend:
            if not self.team_code:
                self.set_unique_team_code()
                self.save()
            return self.team_code
        return self.name

    @property
    def display(self):
        use_team_codes = TabSettings.get("use_team_codes", 0)

        if use_team_codes:
            if not self.team_code:
                self.set_unique_team_code()
                self.save()
            return self.team_code
        return self.name

    def __str__(self):
        return self.display_backend

    def delete(self, using=None, keep_parents=False):
        scratches = Scratch.objects.filter(team=self)
        for scratch in scratches:
            scratch.delete()
        super(Team, self).delete(using, keep_parents)

    def debaters_display(self):
        debaters_public = TabSettings.get("debaters_public", 1)

        if debaters_public:
            return ", ".join([debater.name for debater in self.debaters.all()])
        return ""

    class Meta:
        ordering = ["pk"]


class BreakingTeam(models.Model):
    VARSITY = 0
    NOVICE = 1
    TYPE_CHOICES = (
        (VARSITY, "Varsity"),
        (NOVICE, "Novice")
    )

    team = models.OneToOneField("Team",
                                on_delete=models.CASCADE,
                                related_name="breaking_team")

    seed = models.IntegerField(default=-1)
    effective_seed = models.IntegerField(default=-1)

    type_of_team = models.IntegerField(default=VARSITY,
                                       choices=TYPE_CHOICES)


class Judge(models.Model):
    name = models.CharField(max_length=30, unique=True)
    rank = models.DecimalField(max_digits=4, decimal_places=2)
    email = models.EmailField(blank=True)
    schools = models.ManyToManyField(School)
    ballot_code = models.CharField(max_length=255,
                                   blank=True,
                                   null=True,
                                   unique=True)
    is_dino = models.BooleanField(default=False)
    wing_only = models.BooleanField(default=False)
    required_room_tags = models.ManyToManyField("RoomTag", blank=True)

    def set_unique_ballot_code(self):
        haikunator = Haikunator()
        code = haikunator.haikunate(token_length=0)

        while Judge.objects.filter(ballot_code=code).first():
            code = haikunator.haikunate(token_length=0)

        self.ballot_code = code

    def save(self,
             force_insert=False,
             force_update=False,
             using=None,
             update_fields=None):
        # Generate a random ballot code for judges that don't have one
        if not self.ballot_code:
            self.set_unique_ballot_code()

        super(Judge, self).save(force_insert, force_update, using,
                                update_fields)

    def is_checked_in_for_round(self, round_number):
        return any(checkin.round_number == round_number
                   for checkin in self.checkin_set.all())

    def __str__(self):
        return self.name

    def affiliations_display(self):
        return ", ".join([school.name for school in self.schools.all()
                          if not school.name == ""])

    def delete(self, using=None, keep_parents=False):
        checkins = CheckIn.objects.filter(judge=self)
        for checkin in checkins:
            checkin.delete()
        super(Judge, self).delete(using, keep_parents)

    class Meta:
        ordering = ["name"]


class Scratch(models.Model):
    judge = models.ForeignKey(Judge, related_name="scratches", on_delete=models.CASCADE)
    team = models.ForeignKey(Team, related_name="scratches", on_delete=models.CASCADE)
    TEAM_SCRATCH = 0
    TAB_SCRATCH = 1
    TYPE_CHOICES = (
        (TEAM_SCRATCH, "Discretionary Scratch"),
        (TAB_SCRATCH, "Tab Scratch"),
    )
    scratch_type = models.IntegerField(choices=TYPE_CHOICES)

    class Meta:
        unique_together = ("judge", "team")
        verbose_name_plural = "scratches"

    def __str__(self):
        s_type = ("Team", "Tab")[self.scratch_type]
        return f"{self.team} <={s_type}=> {self.judge}"


class Room(models.Model):
    name = models.CharField(max_length=30, unique=True)
    rank = models.DecimalField(max_digits=4, decimal_places=2)
    tags = models.ManyToManyField("RoomTag", blank=True)

    def __str__(self):
        return self.name

    def delete(self, using=None, keep_parents=False):
        rounds = Round.objects.filter(room=self)
        if rounds.exists():
            raise ValidationError(f"Room is in round: {[str(r) for r in rounds]}")
        super(Room, self).delete(using, keep_parents)

    def is_checked_in_for_round(self, round_number):
        return RoomCheckIn.objects.filter(room=self,
                                          round_number=round_number).exists()

    class Meta:
        ordering = ["name"]


class Outround(models.Model):
    VARSITY = 0
    NOVICE = 1
    TYPE_OF_ROUND_CHOICES = (
        (VARSITY, "Varsity"),
        (NOVICE, "Novice")
    )

    num_teams = models.IntegerField()
    type_of_round = models.IntegerField(default=VARSITY,
                                        choices=TYPE_OF_ROUND_CHOICES)
    gov_team = models.ForeignKey(Team, related_name="gov_team_outround",
                                 on_delete=models.CASCADE)
    opp_team = models.ForeignKey(Team, related_name="opp_team_outround",
                                 on_delete=models.CASCADE)
    chair = models.ForeignKey(Judge,
                              null=True,
                              blank=True,
                              on_delete=models.CASCADE,
                              related_name="chair_outround")
    judges = models.ManyToManyField(Judge, blank=True, related_name="judges_outrounds")
    UNKNOWN = 0
    GOV = 1
    OPP = 2
    GOV_VIA_FORFEIT = 3
    OPP_VIA_FORFEIT = 4
    VICTOR_CHOICES = (
        (UNKNOWN, "UNKNOWN"),
        (GOV, "GOV"),
        (OPP, "OPP"),
        (GOV_VIA_FORFEIT, "GOV via Forfeit"),
        (OPP_VIA_FORFEIT, "OPP via Forfeit"),
    )
    room = models.ForeignKey(Room,
                             on_delete=models.CASCADE,
                             related_name="rooms_outrounds")
    victor = models.IntegerField(choices=VICTOR_CHOICES, default=0)

    sidelock = models.BooleanField(default=False)

    CHOICES = (
        (UNKNOWN, "No"),
        (GOV, "Gov"),
        (OPP, "Opp")
    )
    choice = models.IntegerField(default=UNKNOWN,
                                 choices=CHOICES)

    def clean(self):
        if self.pk and self.chair not in self.judges.all():
            raise ValidationError("Chair must be a judge in the round")

    def __str__(self):
        return (
            f"Outround {self.num_teams} between {self.gov_team} and {self.opp_team}"
        )

    @property
    def winner(self):
        if self.victor in [self.GOV, self.GOV_VIA_FORFEIT]:
            return self.gov_team
        elif self.victor in [2, 4]:
            return self.opp_team
        return None

    @property
    def loser(self):
        if not self.winner:
            return None

        if self.winner == self.gov_team:
            return self.opp_team
        return self.gov_team


class Round(models.Model):
    round_number = models.IntegerField()
    gov_team = models.ForeignKey(Team, related_name="gov_team",
                                 on_delete=models.CASCADE)
    opp_team = models.ForeignKey(Team, related_name="opp_team",
                                 on_delete=models.CASCADE)
    chair = models.ForeignKey(Judge,
                              null=True,
                              blank=True,
                              on_delete=models.CASCADE,
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
    room = models.ForeignKey(
        Room, on_delete=models.SET_NULL, blank=True, null=True)
    victor = models.IntegerField(choices=VICTOR_CHOICES, default=0)

    def clean(self):
        if self.pk and self.chair not in self.judges.all():
            raise ValidationError("Chair must be a judge in the round")

    def __str__(self):
        return (
            f"Round {self.round_number} between {self.gov_team} and {self.opp_team}"
        )

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
    bye_team = models.ForeignKey(Team, related_name="byes", on_delete=models.CASCADE)
    round_number = models.IntegerField()

    def __str__(self):
        return "Bye in round " + str(self.round_number) + " for " + str(
            self.bye_team)


class NoShow(models.Model):
    no_show_team = models.ForeignKey(Team,
                                     related_name="no_shows",
                                     on_delete=models.CASCADE)
    round_number = models.IntegerField()

    @property
    def lenient_late(self):
        """
        Determines if this no-show should be treated leniently based on
        the current tab setting. Returns True if the lenient_late setting
        is greater than or equal to this round number.
        """
        return TabSettings.get("lenient_late", 0) >= self.round_number

    def __str__(self):
        return str(self.no_show_team) + " was no-show for round " + str(
            self.round_number)


class RoundStats(models.Model):
    debater = models.ForeignKey(Debater, on_delete=models.CASCADE)
    round = models.ForeignKey(Round, on_delete=models.CASCADE)
    # fewer digits?
    speaks = models.DecimalField(max_digits=6, decimal_places=4)
    ranks = models.DecimalField(max_digits=6, decimal_places=4)
    debater_role = models.CharField(max_length=4, null=True)

    class Meta:
        verbose_name_plural = "round stats"

    def __str__(self):
        return f"Results for {self.debater} in round {self.round.round_number}"


class CheckIn(models.Model):
    judge = models.ForeignKey(Judge, on_delete=models.CASCADE)
    round_number = models.IntegerField()

    def __str__(self):
        return f"Judge {self.judge} is checked in for round {self.round_number}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["judge", "round_number"],
                name="unique_judge_checkin_per_round",
            )
        ]


class RoomCheckIn(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    round_number = models.IntegerField()

    def __str__(self):
        return f"Room {self.room} is checked in for round {self.round_number}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["room", "round_number"],
                name="unique_room_checkin_per_round",
            )
        ]


class RoomTag(models.Model):
    tag = models.CharField(max_length=255)
    priority = models.DecimalField(max_digits=4, decimal_places=2)

    def __str__(self):
        return self.tag
