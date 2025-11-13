from urllib.parse import urlencode

from django import template
from django.forms.fields import FileField

from mittab.apps.tab.helpers import get_redirect_target
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

@register.simple_tag
def tournament_name():
    return TabSettings.get("tournament_name", "New Tournament")


@register.simple_tag(takes_context=True)
def return_to_value(context):
    request = context.get("request")
    if not request:
        return ""
    return get_redirect_target(request, fallback=None) or ""


@register.inclusion_tag("common/_return_to_input.html", takes_context=True)
def return_to_input(context, target=None):
    redirect_target = target if target is not None else return_to_value(context)
    return {"redirect_target": redirect_target}


@register.filter
def with_return_to(url):
    if not url:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode({'return_to': url})}"


@register.simple_tag
def public_ranking_mode_value():
    return int(get_public_ranking_mode())
