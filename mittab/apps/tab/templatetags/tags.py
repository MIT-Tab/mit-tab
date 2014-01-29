from django import template
from django.core.urlresolvers import reverse

register = template.Library()

@register.simple_tag
def active(request, pattern):
    import re
    print pattern, request.path
    if pattern == request.path:
        return 'active'
    return ''
