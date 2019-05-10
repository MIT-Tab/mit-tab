from django.shortcuts import render, get_object_or_404
from django.template import RequestContext
from django.http import Http404,HttpResponse,HttpResponseRedirect, \
        JsonResponse
from django.contrib.auth.decorators import permission_required
from mittab.apps.tab.forms import JudgeForm, ScratchForm
#New Models based approach
from mittab.apps.tab.models import *
from django.db import models
from mittab.apps.tab.errors import *
from mittab.libs.tab_logic import TabFlags


def view_judges(request):
    #Get a list of (id,school_name) tuples
    current_round = TabSettings.objects.get(key="cur_round").value - 1
    checkins = CheckIn.objects.filter(round_number=current_round)
    checkins_next = CheckIn.objects.filter(round_number=(current_round + 1))
    checked_in_judges = set([c.judge for c in checkins])
    checked_in_judges_next = set([c.judge for c in checkins_next])

    def flags(judge):
        result = 0
        if judge in checked_in_judges:
            result |= TabFlags.JUDGE_CHECKED_IN_CUR
        else:
            result |= TabFlags.JUDGE_NOT_CHECKED_IN_CUR
        if judge in checked_in_judges_next:
            result |= TabFlags.JUDGE_CHECKED_IN_NEXT
        else:
            result |= TabFlags.JUDGE_NOT_CHECKED_IN_NEXT

        if judge.rank < 3.0:
            result |= TabFlags.LOW_RANKED_JUDGE
        if judge.rank >= 3.0 and judge.rank < 5.0:
            result |= TabFlags.MID_RANKED_JUDGE
        if judge.rank >= 5.0:
            result |= TabFlags.HIGH_RANKED_JUDGE
        return result

    c_judge = [(judge.pk, judge.name, flags(judge), "(%s)" % judge.ballot_code)
               for judge in Judge.objects.order_by("name")]

    all_flags = [[TabFlags.JUDGE_CHECKED_IN_CUR, TabFlags.JUDGE_NOT_CHECKED_IN_CUR, TabFlags.JUDGE_CHECKED_IN_NEXT, TabFlags.JUDGE_NOT_CHECKED_IN_NEXT],
                 [TabFlags.LOW_RANKED_JUDGE, TabFlags.MID_RANKED_JUDGE, TabFlags.HIGH_RANKED_JUDGE]]
    filters, symbol_text = TabFlags.get_filters_and_symbols(all_flags)
    return render(request, 'list_data.html', 
                              {
                                  'item_type':'judge',
                                  'title': "Viewing All Judges",
                                  'item_list':c_judge,
                                  'filters': filters,
                              })

def view_judge(request, judge_id):
    judge_id = int(judge_id)
    try:
        judge = Judge.objects.get(pk=judge_id)
    except Judge.DoesNotExist:
        return render(request, 'error.html', 
                                 {'error_type': "View Judge",
                                  'error_name': str(judge_id),
                                  'error_info':"No such judge"})
    if request.method == 'POST':
        form = JudgeForm(request.POST,instance=judge)
        if form.is_valid():
            try:
               form.save()
            except ValueError:
                return render(request, 'error.html', 
                                         {'error_type': "Judge",
                                          'error_name': "["+form.cleaned_data['name']+"]",
                                          'error_info':"Judge information cannot be validated."})
            return render(request, 'thanks.html', 
                                     {'data_type': "Judge",
                                      'data_name': "["+form.cleaned_data['name']+"]",
                                      'data_modification': "EDIT"})
        else :
            return render(request, 'error.html', 
                                     {'error_type': "Judge",
                                      'error_name': "",
                                      'error_info': form.errors})
    else:
        form = JudgeForm(instance=judge)
        base_url = '/judge/'+str(judge_id)+'/'
        scratch_url = base_url + 'scratches/view/'
        delete_url =  base_url + 'delete/'
        links = [(scratch_url, 'Scratches for {}'.format(judge.name), False)]
        return render(request, 'data_entry.html', 
                                 {'form': form,
                                  'links': links,
                                  'title': 'Viewing Judge: {}'.format(judge.name)})

def enter_judge(request):
    if request.method == 'POST':
        form = JudgeForm(request.POST)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                cd = form.cleaned_data
                return render(request, 'error.html',
                                         {'error_type': "Judge",
                                          'error_name': '[{}]'.format(cd['name']),
                                          'error_info': "Judge Cannot Validate!"})
            return render(request, 'thanks.html',
                                     {'data_type': "Judge",
                                      'data_name': "["+form.cleaned_data['name']+"]",
                                      'data_modification': "CREATED",
                                      'enter_again': True})
    else:
        form = JudgeForm(first_entry=True)
    return render(request, 'data_entry.html',
                              {'form': form, 'title': "Create Judge"})

def add_scratches(request, judge_id, number_scratches):
    try:
        judge_id,number_scratches = int(judge_id),int(number_scratches)
    except ValueError:
        return render(request, 'error.html', 
                                 {'error_type': "Scratch",'error_name': "Data Entry",
                                  'error_info':"I require INTEGERS!"})
    try:
        judge = Judge.objects.get(pk=judge_id)
    except Judge.DoesNotExist:
        return render(request, 'error.html', 
                                 {'error_type': "Add Scratches for Judge",
                                  'error_name': str(judge_id),
                                  'error_info':"No such Judge"})
        
    if request.method == 'POST':
        forms = [ScratchForm(request.POST, prefix=str(i)) for i in range(1,number_scratches+1)]
        all_good = True
        for form in forms:
            all_good = all_good and form.is_valid()
        if all_good:
            for form in forms:
                form.save()
            return render(request, 'thanks.html', 
                                     {'data_type': "Scratches for Judge",
                                      'data_name': "["+str(judge_id)+"]",
                                      'data_modification': "CREATED"})
    else:
        forms = [ScratchForm(prefix=str(i), initial={'judge':judge_id,'scratch_type':0}) for i in range(1,number_scratches+1)]
    return render(request, 'data_entry_multiple.html', 
                             {'forms': list(zip(forms,[None]*len(forms))),
                              'data_type':'Scratch',
                              'title':"Adding Scratch(es) for %s"%(judge.name)})

def view_scratches(request, judge_id):
    try:
        judge_id = int(judge_id)
    except ValueError:
        return render(request, 'error.html', 
                                 {'error_type': "Scratch",'error_name': "Delete",
                                  'error_info':"I require INTEGERS!"})
    scratches = Scratch.objects.filter(judge=judge_id)
    judge = Judge.objects.get(pk=judge_id)
    number_scratches = len(scratches)
    if request.method == 'POST':
        forms = [ScratchForm(request.POST, prefix=str(i),instance=scratches[i-1]) for i in range(1,number_scratches+1)]
        all_good = True
        for form in forms:
            all_good = all_good and form.is_valid()
        if all_good:
            for form in forms:
                form.save()
            return render(request, 'thanks.html', 
                                     {'data_type': "Scratches for judge",
                                      'data_name': "["+str(judge_id)+"]",
                                      'data_modification': "EDITED"})
    else:
        forms = [ScratchForm(prefix=str(i), instance=scratches[i-1]) for i in range(1,len(scratches)+1)]
    delete_links = ["/judge/"+str(judge_id)+"/scratches/delete/"+str(scratches[i].id) for i in range(len(scratches))]
    links = [('/judge/'+str(judge_id)+'/scratches/add/1/','Add Scratch',False)]

    return render(request, 'data_entry_multiple.html',
                             {'forms': list(zip(forms,delete_links)),
                              'data_type':'Scratch',
                              'links':links,
                              'title':"Viewing Scratch Information for %s"%(judge.name)})

def batch_checkin(request):
    judges_and_checkins = []

    round_numbers = list([i+1 for i in range(TabSettings.get("tot_rounds"))])
    for judge in Judge.objects.order_by('name'):
        checkins = []
        for round_number in round_numbers:
            checkins.append(judge.is_checked_in_for_round(round_number))
        judges_and_checkins.append((judge, checkins))

    return render(request, 'batch_checkin.html',
            {'judges_and_checkins': judges_and_checkins, 'round_numbers': round_numbers})

@permission_required('tab.tab_settings.can_change', login_url='/403')
def judge_check_in(request, judge_id, round_number):
    judge_id, round_number = int(judge_id), int(round_number)

    if round_number < 1 or round_number > TabSettings.get("tot_rounds"):
        raise Http404("Round does not exist")

    judge = get_object_or_404(Judge, pk=judge_id)
    if request.method == 'POST':
        if not judge.is_checked_in_for_round(round_number):
            check_in = CheckIn(judge=judge, round_number=round_number)
            check_in.save()
    elif request.method == 'DELETE':
        if judge.is_checked_in_for_round(round_number):
            check_ins = CheckIn.objects.filter(judge=judge,
                    round_number=round_number)
            check_ins.delete()
    else:
        raise Http404("Must be POST or DELETE")
    return JsonResponse({'success': True})
