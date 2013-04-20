from django.db import models
from django import forms
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.core.exceptions import ValidationError
from models import *
from decimal import Decimal
import itertools
import pprint

class UploadFileForm(forms.Form):
    file  = forms.FileField(label="Your Backup File")

class SchoolForm(forms.ModelForm):
    class Meta:
        model = School

class RoomForm(forms.ModelForm):
    class Meta:
        model = Room

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

class TeamEntryForm(forms.ModelForm):
    number_scratches = forms.IntegerField(label="How many initial scratches?", initial=0)
    debaters = forms.ModelMultipleChoiceField(queryset=Debater.objects.filter(team__debaters__isnull=True), 
                                              widget=FilteredSelectMultiple("Debaters", 
                                              is_stacked=False))
    def clean_debaters(self):
        data = self.cleaned_data['debaters']
        if not( 1 <= len(data) <= 2) :
            raise forms.ValidationError("You must select 1 or 2 debaters!") 
        return data

    class Meta:
        model = Team
        
class ScratchForm(forms.ModelForm):
    class Meta:
        model = Scratch
        
class DebaterForm(forms.ModelForm):
    class Meta:
        model = Debater
        
        
def validate_speaks(value):
    if not (21.0 <= value <= 29.0 or value == 0):
        raise ValidationError(u'%s is an entirely invalid speaker score, try again.' % value)
    
#TODO: Rewrite this, it is ugly as hell
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
        #TODO: Combine these loops?
        for d in self.GOV:
            self.fields["%s_debater"%(d)] = forms.ChoiceField(label="Who was %s?"%(self.NAMES[d]), choices=gov_debaters)
            self.fields["%s_speaks"%(d)] = forms.DecimalField(label="%s Speaks"%(self.NAMES[d]),validators=[validate_speaks])
            self.fields["%s_ranks"%(d)] = forms.ChoiceField(label="%s Rank"%(self.NAMES[d]), choices=self.RANKS)
        for d in self.OPP:
            self.fields["%s_debater"%(d)] = forms.ChoiceField(label="Who was %s?"%(self.NAMES[d]), choices=opp_debaters)
            self.fields["%s_speaks"%(d)] = forms.DecimalField(label="%s Speaks"%(self.NAMES[d]),validators=[validate_speaks])
            self.fields["%s_ranks"%(d)] = forms.ChoiceField(label="%s Rank"%(self.NAMES[d]), choices=self.RANKS)
        if round_object.victor != 0 and not no_fill:
            for d in self.GOV + self.OPP:
                try:
                    stats = RoundStats.objects.get(round=round_object, debater_role = d)
                    self.fields["%s_debater"%(d)].initial = stats.debater.id
                    self.fields["%s_speaks"%(d)].initial = stats.speaks
                    self.fields["%s_ranks"%(d)].initial = int(round(stats.ranks))
                except:
                    pass

    def clean(self):
        cleaned_data = self.cleaned_data
        #This is where we validate that the person entering data didn't mess up significantly
        gov, opp = self.GOV, self.OPP
        debaters = gov + opp
        try:
            speak_ranks = [ (cleaned_data["%s_speaks" % (d)] ,cleaned_data["%s_ranks" % (d)], d) for d in debaters]
            sorted_by_ranks = sorted(speak_ranks, key=lambda x: x[1])

            #Check to make sure everyone has different ranks
            if set([r[0] for r in self.RANKS]) != set([int(x[1]) for x in sorted_by_ranks]):
                for debater in debaters:
                    self._errors["%s_speaks"%(debater)] = self.error_class(["Ranks must be different"])
            #Check to make sure that the lowest ranks have the highest scores
            high_score = sorted_by_ranks[0][0]
            for (speaks,rank,debater) in sorted_by_ranks:
                if speaks > high_score:
                    self._errors["%s_speaks"%debater] = self.error_class(["These speaks are too high for the rank"])
                high_score = speaks
            #Check to make sure that the team with most points wins
            gov_speaks = sum([cleaned_data["%s_speaks"%(d)] for d in gov])
            opp_speaks = sum([cleaned_data["%s_speaks"%(d)] for d in opp])
            cleaned_data["winner"] = int(cleaned_data["winner"])
            if cleaned_data["winner"] == 0:
                self._errors["winner"] = self.error_class(["Someone has to win!"]) 
            if cleaned_data["winner"] == 1 and opp_speaks > gov_speaks:
                self._errors["winner"] = self.error_class(["Low Point Win!!"])
            if cleaned_data["winner"] == 2 and gov_speaks > opp_speaks:
                self._errors["winner"] = self.error_class(["Low Point Win!!"])
            for deb in gov+opp:
                #TODO: Take out this strange cast to int, perhaps have real error valus?
                if int(cleaned_data["%s_debater"%deb]) == -1:
                    self._errors["%s_debater"%deb] = self.error_class(["You need to pick a debater"])
        except Exception, e:
            print "Caught error %s" %(e)
            self._errors["winner"] = self.error_class(["Non handled error, preventing data contamination"])
        return cleaned_data

    def save(self):
        cleaned_data = self.cleaned_data
        round_obj = Round.objects.get(pk=cleaned_data["round_instance"])
        round_obj.victor = cleaned_data["winner"]
        debaters = self.GOV + self.OPP
        #How do we handle iron men? Do I enter both speaks?
        for debater in debaters:
            old_stats = RoundStats.objects.filter(round=round_obj, debater_role = debater)
            if len(old_stats) > 0:
                old_stats.delete()

            debater_obj = Debater.objects.get(pk=cleaned_data["%s_debater"%(debater)])
            debater_role_obj = debater
            speaks_obj, ranks_obj = float(cleaned_data["%s_speaks"%(debater)]),int(cleaned_data["%s_ranks"%(debater)]) 
            stats = RoundStats(debater = debater_obj, 
                               round = round_obj, 
                               speaks = speaks_obj, 
                               ranks = ranks_obj, 
                               debater_role = debater_role_obj)
            stats.save()
        round_obj.save()
        return round_obj

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
                    key = lambda x: x[2], reverse=True)
    ranked = [(d, rl, s, r+1)
              for (r, (d, rl, s, _)) in enumerate(ranked)]

    print "Ranked Debaters"
    pprint.pprint(ranked)

    # Break any ties by taking the average of the tied ranks
    ties = {}
    for (score_i, score) in enumerate(ranked):
        # For floating point roundoff errors
        d_score = Decimal(score[2]).quantize(Decimal('1.00'))
        if d_score in ties:
            ties[d_score].append((score_i, score[3]))
        else:
            ties[d_score] = [(score_i, score[3])]

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



