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
    def __init__(self, *args, **kwargs):
        super(SchoolForm, self).__init__(*args, **kwargs)

    class Meta:
        model = School
        fields = "__all__"


class RoomForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        entry = "first_entry" in kwargs
        if entry:
            kwargs.pop("first_entry")
        super(RoomForm, self).__init__(*args, **kwargs)
        if not entry:
            num_rounds = TabSettings.objects.get(key="tot_rounds").value
            try:
                room = kwargs["instance"]
                checkins = [
                    c.round_number for c in RoomCheckIn.objects.filter(room=room)
                ]
                for i in range(-1, num_rounds):
                    # 0 is included as zero represents outrounds
                    label = f"Checked in for round {i + 1}?"
                    if i == -1:
                        label = "Checked in for outrounds?"
                    field_name = f"checkin_{i}"
                    self.fields[field_name] = forms.BooleanField(
                        label=label,
                        initial=i + 1 in checkins,
                        required=False)
            except Exception:
                pass

    def save(self, commit=True):
        room = super(RoomForm, self).save(commit)
        num_rounds = TabSettings.objects.get(key="tot_rounds").value
        for i in range(num_rounds):
            field_name = f"checkin_{i}"
            if field_name in self.cleaned_data:
                should_be_checked_in = self.cleaned_data[field_name]
                checked_in = RoomCheckIn.objects.filter(room=room,
                                                        round_number=i + 1)
                # Two cases, either the room is not checked in and the user says he is,
                # or the room is checked in and the user says he is not
                if not checked_in and should_be_checked_in:
                    checked_in = RoomCheckIn(room=room, round_number=i + 1)
                    checked_in.save()
                elif checked_in and not should_be_checked_in:
                    checked_in.delete()

        return room

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
                    # 0 is included as zero represents outrounds
                    label = f"Checked in for round {i + 1}?"
                    if i == -1:
                        label = "Checked in for outrounds?"
                    field_name = f"checkin_{i}"
                    self.fields[field_name] = forms.BooleanField(
                        label=label,
                        initial=i + 1 in checkins,
                        required=False)
            except Exception:
                pass

    def save(self, commit=True):
        judge = super(JudgeForm, self).save(commit)
        num_rounds = TabSettings.objects.get(key="tot_rounds").value
        for i in range(-1, num_rounds):
            field_name = f"checkin_{i}"
            if field_name in self.cleaned_data:
                should_be_checked_in = self.cleaned_data[field_name]
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
               (debater.team_set.count() == 1 and
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
    team = forms.ChoiceField(choices=[])
    judge = forms.ChoiceField(choices=[])
    scratch_type = forms.ChoiceField(choices=Scratch.TYPE_CHOICES)

    def __init__(self, *args, **kwargs):
        team_queryset = kwargs.pop("team_queryset", Team.objects.all())
        judge_queryset = kwargs.pop("judge_queryset", Judge.objects.all())

        super(ScratchForm, self).__init__(*args, **kwargs)

        self.fields["team"].choices = [
            (str(team.id), team.name) for team in team_queryset
        ]
        self.fields["judge"].choices = [
            (str(judge.id), judge.name) for judge in judge_queryset
        ]

        # If we're editing an existing scratch, set initial values
        if self.instance and self.instance.pk:
            self.fields["team"].initial = str(self.instance.team.id)
            self.fields["judge"].initial = str(self.instance.judge.id)

    def save(self, commit=True):
        instance = super(ScratchForm, self).save(commit=False)
        # Convert string IDs to actual model instances
        instance.team = Team.objects.get(pk=int(self.cleaned_data["team"]))
        instance.judge = Judge.objects.get(pk=int(self.cleaned_data["judge"]))
        if commit:
            instance.save()
        return instance

    class Meta:
        model = Scratch
        fields = "__all__"
        exclude = ["team", "judge"]


class JudgeJudgeScratchForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        judge_queryset = kwargs.pop("judge_queryset", Judge.objects.all())
        super().__init__(*args, **kwargs)
        self.fields["judge_one"].queryset = judge_queryset
        self.fields["judge_two"].queryset = judge_queryset

    def clean(self):
        cleaned_data = super().clean()
        judge_one = cleaned_data.get("judge_one")
        judge_two = cleaned_data.get("judge_two")
        if judge_one and judge_two and judge_one == judge_two:
            raise forms.ValidationError("Pick two different judges")
        return cleaned_data

    class Meta:
        model = JudgeJudgeScratch
        fields = "__all__"


class TeamTeamScratchForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        team_queryset = kwargs.pop("team_queryset", Team.objects.all())
        super().__init__(*args, **kwargs)
        self.fields["team_one"].queryset = team_queryset
        self.fields["team_two"].queryset = team_queryset

    def clean(self):
        cleaned_data = super().clean()
        team_one = cleaned_data.get("team_one")
        team_two = cleaned_data.get("team_two")
        if team_one and team_two and team_one == team_two:
            raise forms.ValidationError("Pick two different teams")
        return cleaned_data

    class Meta:
        model = TeamTeamScratch
        fields = "__all__"


class DebaterForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(DebaterForm, self).__init__(*args, **kwargs)

    class Meta:
        model = Debater
        exclude = ["tiebreaker"]


def validate_speaks(value):
    if not (TabSettings.get("min_speak", 0) <= value <= TabSettings.get(
            "max_speak", 50)):
        raise ValidationError(
            f"{value} is an entirely invalid speaker score, try again.")


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
                deb, "debater")] = forms.ChoiceField(
                    label=f"Who was {self.NAMES[deb]}?",
                    choices=debater_choices
                )
            self.fields[self.deb_attr_name(
                deb, "speaks")] = forms.DecimalField(
                    label=f"{self.NAMES[deb]} Speaks",
                    validators=[validate_speaks])
            self.fields[self.deb_attr_name(deb, "ranks")] = forms.ChoiceField(
                label=f"{self.NAMES[deb]} Rank", choices=self.RANKS)

        if round_object.victor == 0 or no_fill:
            return

        for stats in round_object.roundstats_set.all():
            deb = stats.debater_role
            self.fields[self.deb_attr_name(
                deb, "debater")].initial = stats.debater.id
            self.fields[self.deb_attr_name(
                deb, "speaks")].initial = stats.speaks
            self.fields[self.deb_attr_name(deb, "ranks")].initial = int(
                round(stats.ranks))

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
        return f"{position}_{attr}"

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
                msg = (
                    f"This ballot is for round {round_obj.round_number}, "
                    f"but the current round is {cur_round}. "
                    "Go to tab to submit this result."
                )
                self._errors["winner"] = self.error_class([msg])
            else:
                if round_obj.chair.ballot_code != judge.ballot_code:
                    msg = "You are not judging the round, or you are not the chair"
                    self._errors["ballot_code"] = self.error_class([msg])
                elif RoundStats.objects.filter(round=round_obj).first():
                    msg = (
                        "A ballot has already been completed for this round. "
                        "Go to tab if you need to change the results."
                    )
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
            print(f"Caught error {e}")
            self._errors["winner"] = self.error_class(
                ["Non handled error, preventing data contamination"])

        return super(EBallotForm, self).clean()


class SettingsForm(forms.Form):
    def __init__(self, *args, **kwargs):
        settings_to_import = kwargs.pop("settings")
        self.settings = settings_to_import

        super(SettingsForm, self).__init__(*args, **kwargs)

        for setting in self.settings:
            field_name = f"setting_{setting['name']}"
            label = setting["name"].replace("_", " ").title()

            if setting.get("type") == "boolean":
                self.fields[field_name] = forms.BooleanField(
                    label=label,
                    help_text=setting["description"],
                    initial=setting["value"],
                    required=False,
                    widget=forms.CheckboxInput(attrs={
                        "class": "form-check-input"
                    })
                )
            elif setting.get("type") == "choice":
                choices = [(c[0], c[1]) for c in setting.get("choices", [])]
                self.fields[field_name] = forms.TypedChoiceField(
                    label=label,
                    help_text=setting["description"],
                    choices=choices,
                    initial=setting["value"],
                    coerce=int,
                    widget=forms.Select(attrs={
                        "class": "form-control"
                    })
                )
            elif setting.get("type") == "text":
                self.fields[field_name] = forms.CharField(
                    label=label,
                    help_text=setting["description"],
                    initial=setting["value"],
                    required=False,
                    widget=forms.TextInput(attrs={
                        "class": "form-control",
                        "style": "min-width: 300px;"
                    })
                )
            else:
                self.fields[field_name] = forms.IntegerField(
                    label=label,
                    help_text=setting["description"],
                    initial=setting["value"],
                    widget=forms.NumberInput(attrs={
                        "class": "form-control"
                    })
                )

    def save(self):
        for setting in self.settings:
            field = f"setting_{setting['name']}"
            key = setting["name"]

            if "type" in setting and setting["type"] == "boolean":
                value_to_set = 1 if self.cleaned_data[field] else 0
            else:
                value_to_set = self.cleaned_data[field]

            TabSettings.set(key, value_to_set)


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


class OutroundResultEntryForm(forms.Form):
    winner = forms.ChoiceField(label="Which team won the round?",
                               choices=Outround.VICTOR_CHOICES)

    def __init__(self, *args, **kwargs):
        # Have to pop these off before sending to the super constructor
        round_object = kwargs.pop("round_instance")
        no_fill = False
        if "no_fill" in kwargs:
            kwargs.pop("no_fill")
            no_fill = True
        super(OutroundResultEntryForm, self).__init__(*args, **kwargs)
        # If we already have information, fill that into the form
        if round_object.victor != 0 and not no_fill:
            self.fields["winner"].initial = round_object.victor

        self.fields["round_instance"] = forms.IntegerField(
            initial=round_object.pk, widget=forms.HiddenInput())

        if round_object.victor == 0 or no_fill:
            return

    def clean(self):
        cleaned_data = self.cleaned_data
        try:
            if cleaned_data["winner"] == Round.NONE:
                self.add_error("winner",
                               self.error_class(["Someone has to win!"]))

            if self.errors:
                return

        except Exception:
            errors.emit_current_exception()
            self.add_error(
                "winner",
                self.error_class(
                    ["Non handled error, preventing data contamination"]))
        return cleaned_data

    def save(self, _commit=True):
        cleaned_data = self.cleaned_data
        round_obj = Outround.objects.get(pk=cleaned_data["round_instance"])

        round_obj.victor = cleaned_data["winner"]
        round_obj.save()

        round_obj = Outround.objects.get(pk=cleaned_data["round_instance"])
        if round_obj.victor > 0:
            winning_team_seed = round_obj.winner.breaking_team.effective_seed
            losing_team_seed = round_obj.loser.breaking_team.effective_seed

            if losing_team_seed < winning_team_seed:
                round_obj.winner.breaking_team.effective_seed = losing_team_seed
                round_obj.winner.breaking_team.save()

                breaking_team = round_obj.loser.breaking_team
                breaking_team.effective_seed = breaking_team.seed
                breaking_team.save()

        return round_obj

class RoomTagForm(forms.ModelForm):
    teams = forms.ModelMultipleChoiceField(
        queryset=Team.objects.all(),
        required=False,
    )
    judges = forms.ModelMultipleChoiceField(
        queryset=Judge.objects.all(),
        required=False,
    )
    rooms = forms.ModelMultipleChoiceField(
        queryset=Room.objects.all(),
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["teams"].initial = self.instance.team_set.all()
            self.fields["judges"].initial = self.instance.judge_set.all()
            self.fields["rooms"].initial = self.instance.room_set.all()

    def save(self, commit=True):
        room_tag = super().save(commit=commit)

        room_tag.team_set.set(self.cleaned_data.get("teams", []))
        room_tag.judge_set.set(self.cleaned_data.get("judges", []))
        room_tag.room_set.set(self.cleaned_data.get("rooms", []))

        return room_tag

    class Meta:
        model = RoomTag
        fields = ("tag", "priority", "teams", "judges", "rooms")


class MiniRoomTagForm(RoomTagForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("teams")
        self.fields.pop("judges")
        self.fields.pop("rooms")

class BackupForm(forms.Form):
    backup_name = forms.CharField(
        max_length=255,
        label="Backup name",
        widget=forms.TextInput(
            attrs={"placeholder": "Enter backup name", "pattern": r"[^_]*"}
        )
    )
    include_scratches = forms.BooleanField(
        required=False,
        initial=True,
        label="Include scratches"
    )

    def clean_backup_name(self):
        backup_name = self.cleaned_data["backup_name"]
        if "_" in backup_name:
            raise forms.ValidationError(
                "Backup name cannot contain underscores (_)."
            )
        return backup_name
