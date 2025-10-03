from django import template
from django.forms.fields import FileField
from django.utils.safestring import mark_safe

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


@register.filter("bs5_field")
def bs5_field(field, show_label=True):
    """Render a Django form field with Bootstrap 5 classes"""
    if not hasattr(field, 'field'):
        return field
    
    # Add Bootstrap classes to the widget
    css_classes = field.field.widget.attrs.get('class', '')
    if 'form-control' not in css_classes and 'form-select' not in css_classes and 'form-check-input' not in css_classes:
        if hasattr(field.field.widget, 'input_type') and field.field.widget.input_type in ['checkbox', 'radio']:
            css_classes += ' form-check-input'
        elif field.field.widget.__class__.__name__ == 'Select':
            css_classes += ' form-select'
        else:
            css_classes += ' form-control'
    
    field.field.widget.attrs['class'] = css_classes.strip()
    
    # Add is-invalid class if there are errors
    if field.errors:
        field.field.widget.attrs['class'] += ' is-invalid'
    
    output = []
    if show_label and field.label:
        label_class = 'form-check-label' if 'form-check-input' in css_classes else 'form-label'
        output.append(f'<label for="{field.id_for_label}" class="{label_class}">{field.label}</label>')
    
    output.append(str(field))
    
    if field.help_text:
        output.append(f'<div class="form-text">{field.help_text}</div>')
    
    if field.errors:
        output.append('<div class="invalid-feedback d-block">')
        for error in field.errors:
            output.append(str(error))
        output.append('</div>')
    
    return mark_safe(''.join(output))
    