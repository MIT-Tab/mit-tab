import sys, traceback
import itertools
import pprint

from django.db import models, transaction
from django import forms
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.core.exceptions import ValidationError
from decimal import Decimal

from django.utils.safestring import mark_safe

from models import *

class UploadBackupForm(forms.Form):
    file  = forms.FileField(label="Your Backup File")


class UploadDataForm(forms.Form):
    """Creates the form for uploading files."""
    team_file = forms.FileField(label=mark_safe('Teams Data File <br /><small>(Name, School, Seed [full, half, free, '
                                                'none], D1 name, D1 status [varsity, novice, nov, n], D1 phone, '
                                                'D1 prov, <br />D2 name, D2 [varsity, novice, nov, n], D2 phone, '
                                                'D2 prov) <b>Note: Overwrites data for duplicate teams</b></small>'),
                                required=False)
    judge_file = forms.FileField(label=mark_safe('Judge Data File <br /><small>(name, rank, phone #, provider, '
                                                 'school) <b>Note: Overwrites data for duplicate judges</b></small>'),
                                 required=False)
    room_file = forms.FileField(label=mark_safe('Room Data File <br /><small>(name, rank) <b>Note: Overwrites data for '
                                                'duplicate rooms</b></small>'), required=False)


class SchoolForm(forms.ModelForm):
    class Meta:
        model = School
        fields = '__all__'

class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = '__all__'

class JudgeForm(forms.ModelForm):
    schools = forms.ModelMultipleChoiceField(queryset=School.objects.all(),
                                             widget=FilteredSelectMultiple("Affiliated Schools",
                                             is_stacked=False))
    def __init__(self, *args, **kwargs):
        entry = 'first_entry' in kwargs
        if entry:
            kwargs.pop('first_entry')
        super(JudgeForm, self).__init__(*args, **kwargs)
        if not entry:
            num_rounds = TabSettings.objects.get(key="tot_rounds").value
            try:
                judge = kwargs['instance']
                checkins = map(lambda c: c.round_number, CheckIn.objects.filter(judge=judge))
                for i in range(num_rounds):
                    self.fields['checkin_%s' % i] = forms.BooleanField(label ="Checked in for round %s?"%(i+1),
                                                                       initial = i+1 in checkins,
                                                                       required = False)
            except:
                pass

    def save(self, force_insert=False, force_update=False, commit=True):
        judge = super(JudgeForm, self).save(commit)
        num_rounds = TabSettings.objects.get(key="tot_rounds").value
        for i in range(num_rounds):
            if "checkin_%s"%(i) in self.cleaned_data:
                should_be_checked_in = self.cleaned_data['checkin_%s'%(i)]
                checked_in = CheckIn.objects.filter(judge=judge, round_number=i+1)
                # Two cases, either the judge is not checked in and the user says he is,
                # or the judge is checked in and the user says he is not
                if not checked_in and should_be_checked_in:
                    checked_in = CheckIn(judge=judge, round_number=i+1)
                    checked_in.save()
                elif checked_in and not should_be_checked_in:
                    checked_in.delete()

        return judge

    class Meta:
        model = Judge
        fields = '__all__'


class TeamForm(forms.ModelForm):
    debaters = forms.ModelMultipleChoiceField(queryset=Debater.objects.all(), 
                                              widget=FilteredSelectMultiple("Debaters", 
                                              is_stacked=False))   
#    def __init__(self, *args, **kwargs):
#        super(TeamForm, self).__init__(*args, **kwargs)
#        if kwargs.has_key('instance'):
#            instance = kwargs['instance']
#            self.fields['debaters'].initial = [d.pk for d in instance.debaters.all()]

    def clean_debaters(self):
        data = self.cleaned_data['debaters']
        if not( 1 <= len(data) <= 2) :
            raise forms.ValidationError("You must select 1 or 2 debaters!") 
        return data

    class Meta:
        model = Team
        fields = '__all__'

class TeamEntryForm(TeamForm):
    number_scratches = forms.IntegerField(label="How many initial scratches?", initial=0)

    class Meta:
        model = Team
        fields = '__all__'

class ScratchForm(forms.ModelForm):
    team = forms.ModelChoiceField(queryset=Team.objects.all())
    judge = forms.ModelChoiceField(queryset=Judge.objects.all())
    scratch_type = forms.ChoiceField(choices=Scratch.TYPE_CHOICES)

    class Meta:
        model = Scratch
        fields = '__all__'

class DebaterForm(forms.ModelForm):
    class Meta:
        model = Debater
        fields = '__all__'


def validate_speaks(value):
    if not (TabSettings.get("min_speak", 0) <= value <= TabSettings.get("max_speak", 50)):
        raise ValidationError(u'%s is an entirely invalid speaker score, try again.' % value)

class ResultEntryForm(forms.Form):

    NAMES = {
        "pm" : "Prime Minister",
        "mg" : "Member of Government",
        "lo" : "Leader of the Opposition",
        "mo" : "Member of the Opposition"
    }

    GOV = [
        "pm",
        "mg"
    ]

    OPP = [
        "lo",
        "mo"
    ]

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
        round_object = kwargs.pop('round_instance')
        no_fill = False
        if 'no_fill' in kwargs:
            kwargs.pop('no_fill')
            no_fill = True
        super(ResultEntryForm, self).__init__(*args, **kwargs) 
        # If we already have information, fill that into the form
        if round_object.victor != 0 and not no_fill:
            self.fields["winner"].initial = round_object.victor

        self.fields['round_instance'] = forms.IntegerField(initial=round_object.pk,
                                                           widget=forms.HiddenInput())
        gov_team, opp_team = round_object.gov_team, round_object.opp_team
        gov_debaters = [(-1,'---')]+[(d.id, d.name) for d in gov_team.debaters.all()]
        opp_debaters = [(-1,'---')]+[(d.id, d.name) for d in opp_team.debaters.all()]

        for d in self.DEBATERS:
            debater_choices = gov_debaters if d in self.GOV else opp_debaters
            self.fields[self.deb_attr_name(d, "debater")] = forms.ChoiceField(
                    label="Who was %s?" % (self.NAMES[d]),
                    choices=debater_choices
                    )
            self.fields[self.deb_attr_name(d, "speaks")] = forms.DecimalField(
                    label="%s Speaks" % (self.NAMES[d]),
                    validators=[validate_speaks])
            self.fields[self.deb_attr_name(d, "ranks")] = forms.ChoiceField(
                    label="%s Rank" % (self.NAMES[d]),
                    choices=self.RANKS)

        if round_object.victor == 0 or no_fill:
            return

        for d in self.DEBATERS:
            try:
                stats = RoundStats.objects.get(round=round_object, debater_role = d)
                self.fields[self.deb_attr_name(d, "debater")].initial = stats.debater.id
                self.fields[self.deb_attr_name(d, "speaks")].initial = stats.speaks
                self.fields[self.deb_attr_name(d, "ranks")].initial = int(round(stats.ranks))
            except:
                pass

    def clean(self):
        cleaned_data = self.cleaned_data
        gov, opp = self.GOV, self.OPP
        try:
            speak_ranks = [(self.deb_attr_val(d, "speaks"), self.deb_attr_val(d, "ranks"), d) for d in self.DEBATERS]
            sorted_by_ranks = sorted(speak_ranks, key=lambda x: x[1])

            # Check to make sure everyone has different ranks
            if self.has_invalid_ranks():
                for d in self.DEBATERS:
                    self.add_error(self.deb_attr_name(d, "ranks"), self.error_class(["Ranks must be different"]))

            # Check to make sure that the lowest ranks have the highest scores
            high_score = sorted_by_ranks[0][0]
            for (speaks, rank, d) in sorted_by_ranks:
                if speaks > high_score:
                    self.add_error(self.deb_attr_name(d, "speaks"), self.error_class(["These speaks are too high for the rank"]))
                high_score = speaks

            # Check to make sure that the team with most speaks and the least
            # ranks win the round
            gov_speaks = sum([self.deb_attr_val(d, "speaks", float) for d in self.GOV])
            opp_speaks = sum([self.deb_attr_val(d, "speaks", float) for d in self.OPP])
            gov_ranks = sum([self.deb_attr_val(d, "ranks", int) for d in self.GOV])
            opp_ranks = sum([self.deb_attr_val(d, "ranks", int) for d in self.OPP])

            gov_points = (gov_speaks, -gov_ranks)
            opp_points = (opp_speaks, -opp_ranks)

            cleaned_data["winner"] = int(cleaned_data["winner"])

            # No winner, this is bad
            if cleaned_data["winner"] == Round.NONE:
                self.add_error("winner", self.error_class(["Someone has to win!"]))
            # Gov won but opp has higher points
            if cleaned_data["winner"] == Round.GOV and opp_points > gov_points:
                self.add_error("winner", self.error_class(["Low Point Win!!"]))
            # Opp won but gov has higher points
            if cleaned_data["winner"] == Round.OPP and gov_points > opp_points:
                self.add_error("winner", self.error_class(["Low Point Win!!"]))

            # Make sure that all debaters were selected
            for deb in self.DEBATERS:
                if self.deb_attr_val(deb, "debater", int) == -1:
                    self.add_error(self.deb_attr_name(deb, "debater"), self.error_class(["You need to pick a debater"]))

        except Exception, e:
            print "Caught error %s" %(e)
            self.add_error("winner", self.error_class(["Non handled error, preventing data contamination"]))
            traceback.print_exc(file=sys.stdout)
        return cleaned_data

    def save(self):
        cleaned_data = self.cleaned_data
        round_obj = Round.objects.get(pk=cleaned_data["round_instance"])
        round_obj.victor = cleaned_data["winner"]

        with transaction.atomic():
            for debater in self.DEBATERS:
                old_stats = RoundStats.objects.filter(round=round_obj, debater_role=debater)
                if len(old_stats) > 0:
                    old_stats.delete()

                debater_obj = Debater.objects.get(pk=self.deb_attr_val(debater, "debater"))
                stats = RoundStats(debater=debater_obj,
                                   round=round_obj,
                                   speaks=self.deb_attr_val(debater, "speaks", float),
                                   ranks=self.deb_attr_val(debater, "ranks", int),
                                   debater_role=debater)
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
        ranks = [ int(self.deb_attr_val(d, "ranks")) for d in self.DEBATERS ]
        return sorted(ranks) != [1, 2, 3, 4]

def validate_panel(result):
    all_good = True
    all_results = list(itertools.chain(*result.values()))
    debater_roles = zip(*all_results)

    # Check everyone is in the same position
    for debater in debater_roles:
        ds = [(d,rl) for (d,rl,s,r) in debater]
        if not all(x == ds[0] for x in ds):
            all_good = False
            break

    # Check the winner makes sense
    final_winner = max([(len(v), k) for k,v in result.iteritems()])[1]
    debater_roles = zip(*result[final_winner])
    for debater in debater_roles:
        ds = [(d,rl) for (d,rl,s,r) in debater]
        if ((len(ds) != len(result[final_winner])) or
            (len(ds) < 2)):
            all_good = False
            break

    return all_good, "Inconsistent Panel results, please check yourself"

def score_panel(result, discard_minority):
    final_winner = max([(len(v), k) for k,v in result.iteritems()])[1]
    debater_roles = zip(*result[final_winner])

    # Take all speaks and ranks even if they are a minority judge
    if not discard_minority:
        all_results = list(itertools.chain(*result.values()))
        debater_roles = zip(*all_results)

    final_scores = []
    for debater in debater_roles:
        ds = [(d,rl) for (d,rl,s,r) in debater]
        d, rl = ds[0]
        speaks = [s for (d,rl,s,r) in debater]
        avg_speaks = sum(speaks) / float(len(speaks))
        ranks = [r for (d,rl,s,r) in debater]
        avg_ranks = sum(ranks) / float(len(ranks))
        final_scores.append((d, rl, avg_speaks, avg_ranks))

    # Rank by resulting average speaks
    ranked = sorted([score for score in final_scores],
                    key = lambda x: Decimal(x[3]).quantize(Decimal('1.00')))
    ranked = sorted([score for score in ranked],
                    key = lambda x: Decimal(x[2]).quantize(Decimal('1.00')), reverse=True)

    ranked = [(d, rl, s, r+1)
              for (r, (d, rl, s, _)) in enumerate(ranked)]

    print "Ranked Debaters"
    pprint.pprint(ranked)

    # Break any ties by taking the average of the tied ranks
    ties = {}
    for (score_i, score) in enumerate(ranked):
        # For floating point roundoff errors
        d_score = Decimal(score[2]).quantize(Decimal('1.00'))
        d_rank = Decimal(score[3]).quantize(Decimal('1.00'))
        tie_key = (d_score, d_rank)
        if tie_key in ties:
            ties[tie_key].append((score_i, score[3]))
        else:
            ties[tie_key] = [(score_i, score[3])]

    print "Ties"
    pprint.pprint(ties)

    # Average over the tied ranks
    for k, v in ties.iteritems():
        if len(v) > 1:
            tied_ranks = [x[1] for x in v]
            avg = sum(tied_ranks) / float(len(tied_ranks))
            for i, _ in v:
                fs = ranked[i]
                ranked[i] = (fs[0], fs[1], fs[2], avg)
    print "Final scores"
    pprint.pprint(ranked)

    return ranked, final_winner

