import random
import time
import datetime

from django.shortcuts import render
from django.http import HttpResponse, JsonResponse
from django.contrib.auth import logout
from django.contrib.auth.decorators import permission_required
from django.db import transaction
from django.shortcuts import redirect

from mittab.apps.tab.helpers import redirect_and_flash_error, \
        redirect_and_flash_success
from mittab.apps.tab.team_views import get_team_rankings
from mittab.apps.tab.models import *
from mittab.libs.errors import *
from mittab.apps.tab.forms import ResultEntryForm, UploadBackupForm, score_panel, \
        validate_panel, EBallotForm
import mittab.libs.cache_logic as cache_logic
import mittab.libs.tab_logic as tab_logic
import mittab.libs.assign_judges as assign_judges
import mittab.libs.backup as backup


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def break_teams(request):
    if request.method == "POST":
        # Perform the break
        teams, nov_teams = cache_logic.cache_fxn_key(
            get_team_rankings,
            "team_rankings",
            request
        )
        
        nov_teams_to_break = TabSettings.get("nov_teams_to_break", 4)
        var_teams_to_break = TabSettings.get("var_teams_to_break", 8)
        
        # This forces a refresh of the breaking teams
        BreakingTeam.objects.all().delete()
    
        current_seed = 1
        for team in teams:
            if current_seed > var_teams_to_break:
                break

            BreakingTeam.objects.create(team=team[0],
                                        seed=current_seed,
                                        type_of_team=BreakingTeam.VARSITY)
            current_seed += 1

        current_seed = 1
        for nov_team in nov_teams:
            if current_seed > nov_teams_to_break:
                break
            
            if BreakingTeam.objects.filter(team=nov_team[0]).exists():
                continue
            
            BreakingTeam.objects.create(team=nov_team[0],
                                    seed=current_seed,
                                        type_of_team=BreakingTeam.NOVICE)

            current_seed += 1

        return redirect_and_flash_success(
            request, "Success!", path="/"
        )

    # See if we can break
    return redirect_and_flash_error(
        request, "PLACEHOLDER", path="/"
    )
