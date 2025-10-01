import csv
import random
from django.http import HttpResponse
from mittab.apps.tab.models import (
    Bye, Team, TabSettings, Outround, BreakingTeam
)
from mittab.libs import tab_logic

def export_pairings_csv(is_outround=False, type_of_round=None):
    response = HttpResponse(content_type="text/csv")
    writer = csv.writer(response)
    headers = (["Round"] if is_outround else []) + [
        "Government", "Opposition", "Judge", "Room"]

    if is_outround:
        key = "var_teams_visible" if type_of_round == BreakingTeam.VARSITY \
              else "nov_teams_visible"
        rnum = TabSettings.get(key, 256)
        filename = "outround_pairings.csv"
        values = sorted(set(Outround.objects.filter(
            num_teams__gte=rnum, type_of_round=type_of_round
        ).values_list("num_teams", flat=True)), reverse=True)
        round_type = "N" if type_of_round else "V"
        groups = [(f"[{round_type}] Ro{v}",
                   [p for p in tab_logic.sorted_pairings(v, True)
                    if p.type_of_round == type_of_round]) for v in values]
    else:
        rnum = TabSettings.get("cur_round") - 1
        filename = f"round_{rnum}_pairings.csv"
        pairings = tab_logic.sorted_pairings(rnum)
        random.seed(0xBEEF)
        random.shuffle(pairings)
        pairings.sort(key=lambda r: r.gov_team.name if r.gov_team else "")
        groups = [(None, pairings)]

    response["Content-Disposition"] = f"attachment; filename={filename}"
    writer.writerow(headers)

    for label, pairings in groups:
        for pairing in pairings:
            judges = ", ".join([f"{j.name} (chair)" if hasattr(pairing, "chair")
                                and j == pairing.chair else j.name
                                for j in pairing.judges.all() if j.name]) \
                     if hasattr(pairing, "judges") and pairing.judges.exists() else ""

            row = [getattr(pairing.gov_team, "display", ""),
                   getattr(pairing.opp_team, "display", ""),
                   judges, getattr(pairing.room, "name", "")]
            writer.writerow([label] + row if is_outround else row)

        if not is_outround:
            paired_ids = {p.gov_team_id for p in pairings if p.gov_team_id} | \
                        {p.opp_team_id for p in pairings if p.opp_team_id}
            bye_ids = set(Bye.objects.filter(round_number=rnum)
                          .values_list("bye_team_id", flat=True))

            bye_teams = [b.bye_team.display for b in Bye.objects.filter(
                round_number=rnum).select_related("bye_team")]
            unpaired_teams = [t.display for t in Team.objects.filter(
                checked_in=True).exclude(id__in=paired_ids | bye_ids)]
            for lbl, teams in [("BYES", bye_teams), ("UNPAIRED", unpaired_teams)]:
                if teams:
                    writer.writerow([""] * 4)
                    writer.writerow([lbl] + [""] * 3)
                    for team in teams:
                        writer.writerow([team] + [""] * 3)
    return response
