from django import template
from django.forms.fields import FileField
from mittab.apps.tab.models import TabSettings

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


@register.filter("is_select_field")
def is_select_field(field):
    """Check if a field is a select/dropdown field"""
    if not hasattr(field, "field"):
        return False
    widget_name = field.field.widget.__class__.__name__
    return 'Select' in widget_name or getattr(field.field.widget, 'input_type', None) == 'select'


@register.filter("is_checkbox_field")
def is_checkbox_field(field):
    """Check if a field is a checkbox field"""
    if not hasattr(field, "field"):
        return False
    return getattr(field.field.widget, 'input_type', None) == 'checkbox'


@register.filter("is_checked_in")
def is_checked_in(judge, round_value):
    return judge.is_checked_in_for_round(round_value)


@register.simple_tag(takes_context=True)
def judge_team_count(context, judge, pairing):
    judge_rejudge_counts = context.get("judge_rejudge_counts", {})
    if judge_rejudge_counts and judge.id in judge_rejudge_counts:
        return judge_rejudge_counts[judge.id].get(pairing.id)
    return None

@register.simple_tag
def tournament_name():
    return TabSettings.get("tournament_name", "New Tournament")
    