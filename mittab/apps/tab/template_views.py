from django.views.generic import TemplateView


class ExportXlsView(TemplateView):
    """Shows the template 'export_links.html'"""
    template_name = 'export_links.html'
