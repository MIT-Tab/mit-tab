"""Render Django bound fields with MIT-Tab's form styles."""

from django import forms, template

register = template.Library()


@register.inclusion_tag("ui/form.html")
def render_form(form, files_last=False):
    return {"form": form, "files_last": files_last}


@register.filter
def uses_file_input(field):
    return isinstance(field.field.widget, forms.FileInput)


@register.inclusion_tag("ui/field.html")
def render_field(field, compact=False):
    widget = field.field.widget
    checkbox = isinstance(widget, forms.CheckboxInput)
    choices = isinstance(widget, (forms.RadioSelect, forms.CheckboxSelectMultiple))
    style = "form-control"
    if checkbox:
        style = "form-check-input"
    elif choices:
        style = "form-choices"
    elif uses_file_input(field):
        style = "form-control-file"

    field_id = widget.attrs.get("id") or field.auto_id
    help_id = f"{field_id}_helptext" if field_id and field.help_text else ""
    error_id = f"{field_id}_error" if field_id and field.errors else ""
    attrs = {"class": f'{widget.attrs.get("class", "")} {style}'.strip()}
    if compact:
        attrs["class"] += " form-control-sm"
    if field.errors:
        attrs["class"] += " is-invalid"
        attrs["aria-invalid"] = "true"
    description = " ".join(filter(None, (widget.attrs.get("aria-describedby"), help_id, error_id)))
    if description:
        attrs["aria-describedby"] = description

    return {
        "field": field,
        "checkbox": checkbox,
        "choices": choices,
        "help_id": help_id,
        "error_id": error_id,
        "widget_html": field.as_widget(attrs=attrs) if not field.is_hidden else field.as_widget(),
    }
