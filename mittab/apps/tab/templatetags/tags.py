from django import template
from django.forms.fields import FileField

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

@register.simple_tag
def fields_for_category(form, category_id):
    """Return form fields for a specific category"""
    return form.fields_for_category(category_id)

@register.filter
def format_setting_name(value):
    """Convert setting_name to Title Case"""
    if not value:
        return value
    if value.startswith("setting_"):
        value = value[8:]
    return value.replace("_", " ").title()
