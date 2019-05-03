from django import template
from django.core.urlresolvers import reverse

register = template.Library()

@register.simple_tag
def active(request, pattern):
    try:
        request.path
    except:
        import pdb; pdb.set_trace()
    if pattern == request.path:
        return 'active'
    return ''
