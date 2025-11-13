from django import template
from django.forms.fields import FileField
from mittab.apps.tab.models import TabSettings
from mittab.apps.tab.public_rankings import get_public_ranking_mode

register = template.Library()


@register.simple_tag
def active(request, pattern):
    if request and pattern == request.path:
        return "active"
    return ""


@register.inclusion_tag("common/_quick_search.html")
def quick_search():
    return {}


@register.inclusion_tag("ballots/_form.html")
def round_form(form, gov_team, opp_team):
    return {"form": form, "gov_team": gov_team, "opp_team": opp_team}


@register.inclusion_tag("outrounds/_form.html")
def outround_form(form):
    return {"form": form}


@register.filter("is_file_field")
def is_file_field(field):
    if not hasattr(field, "field"):
        return False
    return isinstance(field.field, FileField)


@register.filter("is_checked_in")
def is_checked_in(judge, round_value):
    return judge.is_checked_in_for_round(round_value)


@register.simple_tag(takes_context=True)
def judge_team_count(context, judge, pairing):
    judge_rejudge_counts = context.get("judge_rejudge_counts", {})
    if judge_rejudge_counts and judge.id in judge_rejudge_counts:
        return judge_rejudge_counts[judge.id].get(pairing.id)
    return None


@register.simple_tag(takes_context=True)
def is_manual_judge_assignment(context, round_id, judge_id):
    manual_assignments = context.get("manual_judge_assignments", {})
    judges_for_round = manual_assignments.get(round_id, set())
    return judge_id in judges_for_round

@register.simple_tag
def tournament_name():
    return TabSettings.get("tournament_name", "New Tournament")


@register.simple_tag
def public_ranking_mode_value():
    return int(get_public_ranking_mode())
