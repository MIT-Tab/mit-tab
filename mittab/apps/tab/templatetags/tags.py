from django import template
from django.core.urlresolvers import reverse

register = template.Library()

@register.simple_tag
def active(request, pattern):
    if request and pattern == request.path:
        return 'active'
    return ''

@register.inclusion_tag('common/_quick_search.html')
def quick_search():
    return {}


@register.inclusion_tag('ballots/_form.html')
def round_form(form, gov_team, opp_team):
    return { 'form': form, 'gov_team': gov_team, 'opp_team': opp_team }
