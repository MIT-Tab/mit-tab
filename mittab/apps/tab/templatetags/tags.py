from django import template

register = template.Library()


@register.simple_tag
def active(request, pattern):
    if pattern == request.path:
        return 'active'
    return ''
