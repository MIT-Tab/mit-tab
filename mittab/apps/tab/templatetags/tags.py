from django import template
from django.core.urlresolvers import reverse

register = template.Library()

@register.simple_tag
def active(request, pattern):
    import re
    if pattern == request.path:
        return 'active'
    return ''

@register.inclusion_tag('round_form.html')
def round_form(form, gov_team, opp_team):
    print('Registering!')
    return { 'form': form, 'gov_team': gov_team, 'opp_team': opp_team }
