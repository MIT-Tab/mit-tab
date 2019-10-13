from decimal import Decimal
import os
import itertools
import pprint

from django.db import transaction
from django import forms
from django.core.exceptions import ValidationError

from mittab.apps.tab.models import *
from mittab.libs import errors, cache_logic
from mittab import settings


class UploadBackupForm(forms.Form):
    file = forms.FileField(label="Your Backup File")


class UploadDataForm(forms.Form):
    team_file = forms.FileField(label="Teams Data File", required=False)
    judge_file = forms.FileField(label="Judge Data File", required=False)
    room_file = forms.FileField(label="Room Data File", required=False)
    scratch_file = forms.FileField(label="Scratch Data File", required=False)


class SchoolForm(forms.ModelForm):
    class Meta:
        model = School
        fields = "__all__"


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = "__all__"


class JudgeForm(forms.ModelForm):
    schools = forms.ModelMultipleChoiceField(queryset=School.objects.all(),
                                             required=False)

    def __init__(self, *args, **kwargs):
        entry = "first_entry" in kwargs
        if entry:
            kwargs.pop("first_entry")
        super(JudgeForm, self).__init__(*args, **kwargs)
        if not entry:
            num_rounds = TabSettings.objects.get(key="tot_rounds").value
            try:
                judge = kwargs["instance"]
                checkins = [
                    c.round_number for c in CheckIn.objects.filter(judge=judge)
                ]
                for i in range(-1, num_rounds):
                    label = "Checked in for round %s?" % (i + 1)
                    if i == -1:
                        label = "Checked in for outrounds?"
                    self.fields["checkin_%s" % i] = forms.BooleanField(
                        label=label,
                        initial=i + 1 in checkins,
                        required=False)
            except Exception:
                pass

    def save(self, commit=True):
        judge = super(JudgeForm, self).save(commit)
        num_rounds = TabSettings.objects.get(key="tot_rounds").value
        for i in range(-1, num_rounds):
            if "checkin_%s" % (i) in self.cleaned_data:
                should_be_checked_in = self.cleaned_data["checkin_%s" % (i)]
                checked_in = CheckIn.objects.filter(judge=judge,
                                                    round_number=i + 1)
                # Two cases, either the judge is not checked in and the user says he is,
                # or the judge is checked in and the user says he is not
                if not checked_in and should_be_checked_in:
                    checked_in = CheckIn(judge=judge, round_number=i + 1)
                    checked_in.save()
                elif checked_in and not should_be_checked_in:
                    checked_in.delete()

        return judge

    class Meta:
        model = Judge
        fields = "__all__"

    class Media:
        css = {
            "all": (os.path.join(settings.BASE_DIR,
                                 "/static/admin/css/widgets.css"), ),
        }
        js = ("/admin/jsi18n"),


class TeamForm(forms.ModelForm):
    debaters = forms.ModelMultipleChoiceField(queryset=Debater.objects.all(),
                                              required=False)

    def clean_debaters(self):
        data = self.cleaned_data["debaters"]
        if len(data) not in [1, 2]:
            raise forms.ValidationError("You must select 1 or 2 debaters!")
        for debater in data:
            if debater.team_set.count() > 1 or \
               (debater.team_set.count() == 1 and \
                debater.team_set.first().id != self.instance.id):
                raise forms.ValidationError(
                    """A debater cannot already be on a different team!
                    Consider editing the debaters' existing team,
                     or removing them from it before creating this one."""
                )
        return data

    class Meta:
        model = Team
        exclude = ["tiebreaker"]

    class Media:
        css = {
            "all": (os.path.join(settings.BASE_DIR,
                                 "/static/admin/css/widgets.css"), ),
        }
        js = ("/admin/jsi18n"),


class TeamEntryForm(TeamForm):
    number_scratches = forms.IntegerField(label="How many initial scratches?",
                                          initial=0)

    def clean_debaters(self):
        data = self.cleaned_data["debaters"]
        for debater in data:
            if debater.team_set.count() > 0:
                raise forms.ValidationError(
                    """A debater cannot already be on a different team!
                    Consider editing the debaters' existing team,
                     or removing them from it before creating this one."""
                )
        return data

    class Meta:
        model = Team
        exclude = ["tiebreaker"]


class ScratchForm(forms.ModelForm):
    team = forms.ModelChoiceField(queryset=Team.objects.all())
    judge = forms.ModelChoiceField(queryset=Judge.objects.all())
    scratch_type = forms.ChoiceField(choices=Scratch.TYPE_CHOICES)

    class Meta:
        model = Scratch
        fields = "__all__"


class DebaterForm(forms.ModelForm):
    class Meta:
        model = Debater
        exclude = ["tiebreaker"]


def validate_speaks(value):
    if not (TabSettings.get("min_speak", 0) <= value <= TabSettings.get(
            "max_speak", 50)):
        raise ValidationError(
            "%s is an entirely invalid speaker score, try again." % value)


class ResultEntryForm(forms.Form):

    NAMES = {
        "pm": "Prime Minister",
        "mg": "Member of Government",
        "lo": "Leader of the Opposition",
        "mo": "Member of the Opposition"
    }

    GOV = ["pm", "mg"]

    OPP = ["lo", "mo"]

    DEBATERS = GOV + OPP

    RANKS = (
        (1, 1),
        (2, 2),
        (3, 3),
        (4, 4),
    )

    winner = forms.ChoiceField(label="Which team won the round?",
                               choices=Round.VICTOR_CHOICES)

    def __init__(self, *args, **kwargs):
        # Have to pop these off before sending to the super constructor
        round_object = kwargs.pop("round_instance")
        no_fill = False
        if "no_fill" in kwargs:
            kwargs.pop("no_fill")
            no_fill = True
        super(ResultEntryForm, self).__init__(*args, **kwargs)
        # If we already have information, fill that into the form
        if round_object.victor != 0 and not no_fill:
            self.fields["winner"].initial = round_object.victor

        self.fields["round_instance"] = forms.IntegerField(
            initial=round_object.pk, widget=forms.HiddenInput())
        gov_team, opp_team = round_object.gov_team, round_object.opp_team
        gov_debaters = [(-1, "---")] + [(d.id, d.name)
                                        for d in gov_team.debaters.all()]
        opp_debaters = [(-1, "---")] + [(d.id, d.name)
                                        for d in opp_team.debaters.all()]

        for deb in self.DEBATERS:
            debater_choices = gov_debaters if deb in self.GOV else opp_debaters
            self.fields[self.deb_attr_name(
                deb, "debater")] = forms.ChoiceField(label="Who was %s?" %
                                                     (self.NAMES[deb]),
                                                     choices=debater_choices)
            self.fields[self.deb_attr_name(
                deb, "speaks")] = forms.DecimalField(
                    label="%s Speaks" % (self.NAMES[deb]),
                    validators=[validate_speaks])
            self.fields[self.deb_attr_name(deb, "ranks")] = forms.ChoiceField(
                label="%s Rank" % (self.NAMES[deb]), choices=self.RANKS)

        if round_object.victor == 0 or no_fill:
            return

        for deb in self.DEBATERS:
            try:
                stats = RoundStats.objects.get(round=round_object,
                                               debater_role=deb)
                self.fields[self.deb_attr_name(
                    deb, "debater")].initial = stats.debater.id
                self.fields[self.deb_attr_name(
                    deb, "speaks")].initial = stats.speaks
                self.fields[self.deb_attr_name(deb, "ranks")].initial = int(
                    round(stats.ranks))
            except Exception:
                pass

    def clean(self):
        cleaned_data = self.cleaned_data
        try:
            speak_ranks = [(self.deb_attr_val(d, "speaks"),
                            self.deb_attr_val(d, "ranks"), d)
                           for d in self.DEBATERS]
            sorted_by_ranks = sorted(speak_ranks, key=lambda x: x[1])

            # Check to make sure everyone has different ranks
            if self.has_invalid_ranks():
                for deb in self.DEBATERS:
                    self.add_error(
                        self.deb_attr_name(deb, "ranks"),
                        self.error_class(["Ranks must be different"]))

            # Check to make sure that the lowest ranks have the highest scores
            high_score = sorted_by_ranks[0][0]
            for (speaks, _rank, deb) in sorted_by_ranks:
                if speaks > high_score:
                    self.add_error(
                        self.deb_attr_name(deb, "speaks"),
                        self.error_class(
                            ["These speaks are too high for the rank"]))
                high_score = speaks

            # Make sure that all debaters were selected
            for deb in self.DEBATERS:
                if self.deb_attr_val(deb, "debater", int) == -1:
                    self.add_error(
                        self.deb_attr_name(deb, "debater"),
                        self.error_class(["You need to pick a debater"]))

            cleaned_data["winner"] = int(cleaned_data["winner"])

            if cleaned_data["winner"] == Round.NONE:
                self.add_error("winner",
                               self.error_class(["Someone has to win!"]))

            # If we already have errors, don't bother with the other validations
            if self.errors:
                return

            # Check to make sure that the team with most speaks and the least
            # ranks win the round
            gov_speaks = sum(
                [self.deb_attr_val(d, "speaks", float) for d in self.GOV])
            opp_speaks = sum(
                [self.deb_attr_val(d, "speaks", float) for d in self.OPP])
            gov_ranks = sum(
                [self.deb_attr_val(d, "ranks", int) for d in self.GOV])
            opp_ranks = sum(
                [self.deb_attr_val(d, "ranks", int) for d in self.OPP])

            gov_points = (gov_speaks, -gov_ranks)
            opp_points = (opp_speaks, -opp_ranks)
            if cleaned_data["winner"] == Round.GOV and opp_points > gov_points:
                self.add_error("winner", self.error_class(["Low Point Win!!"]))
            elif cleaned_data[
                    "winner"] == Round.OPP and gov_points > opp_points:
                self.add_error("winner", self.error_class(["Low Point Win!!"]))

        except Exception:
            errors.emit_current_exception()
            self.add_error(
                "winner",
                self.error_class(
                    ["Non handled error, preventing data contamination"]))
        return cleaned_data

    def save(self, _commit=True):
        cleaned_data = self.cleaned_data
        round_obj = Round.objects.get(pk=cleaned_data["round_instance"])

        if not round_obj.victor == cleaned_data["winner"]:
            cache_logic.invalidate_cache("team_rankings")
            cache_logic.invalidate_cache("speaker_rankings")

        round_obj.victor = cleaned_data["winner"]

        with transaction.atomic():
            for debater in self.DEBATERS:
                old_stats = RoundStats.objects.filter(round=round_obj,
                                                      debater_role=debater)
                debater_obj = Debater.objects.get(
                    pk=self.deb_attr_val(debater, "debater"))
                stats = RoundStats(
                    debater=debater_obj,
                    round=round_obj,
                    speaks=self.deb_attr_val(debater, "speaks", float),
                    ranks=self.deb_attr_val(debater, "ranks", int),
                    debater_role=debater)

                if old_stats.exists():
                    for old_stat in old_stats:
                        if (old_stat.debater != stats.debater) or \
                           (old_stat.speaks != stats.speaks) or \
                           (old_stat.ranks != stats.ranks) or \
                           (old_stat.debater_role != stats.debater_role) or \
                           (old_stat.round != stats.round):
                            cache_logic.invalidate_cache("team_rankings")
                            cache_logic.invalidate_cache("speaker_rankings")
                    old_stats.delete()
                stats.save()
            round_obj.save()
        return round_obj

    def deb_attr_val(self, position, attr, cast=None):
        val = self.cleaned_data[self.deb_attr_name(position, attr)]
        if cast:
            return cast(val)
        else:
            return val

    def deb_attr_name(self, position, attr):
        return "%s_%s" % (position, attr)

    def has_invalid_ranks(self):
        ranks = [int(self.deb_attr_val(d, "ranks")) for d in self.DEBATERS]
        return sorted(ranks) != [1, 2, 3, 4]


class EBallotForm(ResultEntryForm):
    ballot_code = forms.CharField(max_length=30, min_length=0)

    def __init__(self, *args, **kwargs):
        ballot_code = ""

        if "ballot_code" in kwargs:
            ballot_code = kwargs.pop("ballot_code")

        super(EBallotForm, self).__init__(*args, **kwargs)
        self.fields["ballot_code"].initial = ballot_code

    def clean(self):
        cleaned_data = self.cleaned_data
        round_obj = Round.objects.get(pk=cleaned_data["round_instance"])
        cur_round = TabSettings.get("cur_round", 0) - 1

        try:
            ballot_code = cleaned_data.get("ballot_code")
            judge = Judge.objects.filter(ballot_code=ballot_code).first()

            if not judge:
                msg = "Incorrect ballot code. Enter again."
                self._errors["ballot_code"] = self.error_class([msg])
            elif round_obj.round_number != cur_round:
                msg = """
                      This ballot is for round %d, but the current round is %d.
                      Go to tab to submit this result.
                      """ % (round_obj.round_number, cur_round)
                self._errors["winner"] = self.error_class([msg])
            else:
                if round_obj.chair.ballot_code != judge.ballot_code:
                    msg = "You are not judging the round, or you are not the chair"
                    self._errors["ballot_code"] = self.error_class([msg])
                elif RoundStats.objects.filter(round=round_obj).first():
                    msg = """
                          A ballot has already been completed for this round.
                          Go to tab if you need to change the results.
                          """
                    self._errors["ballot_code"] = self.error_class([msg])

            if int(cleaned_data["winner"]) not in [Round.GOV, Round.OPP]:
                msg = "Go to tab to submit a result other than a win or loss."
                self._errors["winner"] = self.error_class([msg])

            for deb in self.DEBATERS:
                speaks = self.deb_attr_val(deb, "speaks", float)
                _, decimal_val = str(speaks).split(".")
                key = self.deb_attr_name(deb, "speaks")
                if int(decimal_val) != 0:
                    msg = "Speaks must be whole numbers"
                    self._errors[key] = self.error_class([msg])
                if speaks > float(TabSettings.get("max_eballot_speak", 35)) or \
                        speaks < float(TabSettings.get("min_eballot_speak", 15)):
                    msg = "Speaks must be justified to tab."
                    self._errors[key] = self.error_class([msg])

        except Exception as e:
            print(("Caught error %s" % e))
            self._errors["winner"] = self.error_class(
                ["Non handled error, preventing data contamination"])

        return super(EBallotForm, self).clean()


class SettingsForm(forms.Form):
    def __init__(self, *args, **kwargs):
        settings_to_import = kwargs.pop("settings")
        self.settings = settings_to_import

        super(SettingsForm, self).__init__(*args, **kwargs)

        for setting in self.settings:
            if "type" in setting and setting["type"] == "boolean":
                self.fields["setting_%s" % (setting["name"],)] = forms.BooleanField(
                    label=setting["name"],
                    help_text=setting["description"],
                    initial=setting["value"],
                    required=False
                )
            else:
                self.fields["setting_%s" % (setting["name"],)] = forms.IntegerField(
                    label=setting["name"],
                    help_text=setting["description"],
                    initial=setting["value"]
                )

    def save(self):
        for setting in self.settings:
            field = "setting_%s" % (setting["name"],)
            tab_setting = TabSettings.objects.filter(
                key=self.fields[field].label
            ).first()

            value_to_set = setting["value"]

            if "type" in setting and setting["type"] == "boolean":
                if not self.cleaned_data[field]:
                    value_to_set = 0
                else:
                    value_to_set = 1
            else:
                value_to_set = self.cleaned_data[field]

            if not tab_setting:
                tab_setting = TabSettings.objects.create(key=self.fields[field].label,
                                                         value=value_to_set)
            else:
                tab_setting.value = value_to_set
                tab_setting.save()


def validate_panel(result):
    all_good = True
    all_results = list(itertools.chain(*list(result.values())))
    debater_roles = list(zip(*all_results))

    # Check everyone is in the same position
    for debater in debater_roles:
        debs = [(deb, role) for (deb, role, _speak, _rank) in debater]
        if not all(deb == debs[0] for deb in debs):
            all_good = False
            break

    # Check the winner makes sense
    final_winner = max([(len(v), k) for k, v in result.items()])[1]
    debater_roles = list(zip(*result[final_winner]))
    for debater in debater_roles:
        debs = [(deb, role) for (deb, role, _speak, _rank) in debater]
        if ((len(debs) != len(result[final_winner])) or (len(debs) < 2)):
            all_good = False
            break

    return all_good, "Inconsistent Panel results, please check yourself"


def score_panel(result, discard_minority):
    final_winner = max([(len(v), k) for k, v in result.items()])[1]
    debater_roles = list(zip(*result[final_winner]))

    # Take all speaks and ranks even if they are a minority judge
    if not discard_minority:
        all_results = list(itertools.chain(*list(result.values())))
        debater_roles = list(zip(*all_results))

    final_scores = []
    for debater in debater_roles:
        debs = [(deb, role) for (deb, role, _speak, _rank) in debater]
        deb, role = debs[0]
        speaks = [speak for (_deb, _role, speak, _rank) in debater]
        avg_speaks = sum(speaks) / float(len(speaks))
        ranks = [rank for (_deb, _role, _speak, rank) in debater]
        avg_ranks = sum(ranks) / float(len(ranks))
        final_scores.append((deb, role, avg_speaks, avg_ranks))

    # Rank by resulting average speaks
    ranked = sorted([score for score in final_scores],
                    key=lambda x: Decimal(x[3]).quantize(Decimal("1.00")))
    ranked = sorted([score for score in ranked],
                    key=lambda x: Decimal(x[2]).quantize(Decimal("1.00")),
                    reverse=True)

    ranked = [(deb, role, speak, rank + 1)
              for (rank, (deb, role, speak, _)) in enumerate(ranked)]

    print("Ranked Debaters")
    pprint.pprint(ranked)

    # Break any ties by taking the average of the tied ranks
    ties = {}
    for (score_i, score) in enumerate(ranked):
        # For floating point roundoff errors
        d_score = Decimal(score[2]).quantize(Decimal("1.00"))
        d_rank = Decimal(score[3]).quantize(Decimal("1.00"))
        tie_key = (d_score, d_rank)
        if tie_key in ties:
            ties[tie_key].append((score_i, score[3]))
        else:
            ties[tie_key] = [(score_i, score[3])]

    print("Ties")
    pprint.pprint(ties)

    # Average over the tied ranks
    for val in ties.values():
        if len(val) > 1:
            tied_ranks = [rank for _score_i, rank in val]
            avg = sum(tied_ranks) / float(len(tied_ranks))
            for i, _rank in val:
                final_score = ranked[i]
                ranked[i] = (final_score[0], final_score[1], final_score[2],
                             avg)
    print("Final scores")
    pprint.pprint(ranked)

    return ranked, final_winner
