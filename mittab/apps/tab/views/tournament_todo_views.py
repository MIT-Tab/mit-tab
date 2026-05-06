from django.contrib.auth.views import LoginView
from django.http import JsonResponse
from django.shortcuts import redirect, render, reverse

from mittab.apps.tab.helpers import redirect_and_flash_success
from mittab.apps.tab.models import UserTournamentSetupPreference
from mittab.libs.tournament_todo import (
    get_field_to_step_map,
    get_tournament_todo_sections,
    get_tournament_todo_steps,
    set_step_checked,
)


TRUTHY_CHECKBOX_VALUES = {"1", "true", "on", "yes"}


def parse_checkbox_value(raw_value):
    return str(raw_value or "").strip().lower() in TRUTHY_CHECKBOX_VALUES


class StaffLoginView(LoginView):
    template_name = "public/staff_login.html"

    def get_success_url(self):
        redirect_url = self.get_redirect_url()
        if redirect_url:
            return redirect_url

        preference, _ = UserTournamentSetupPreference.objects.get_or_create(
            user=self.request.user,
        )
        if not preference.hide_tournament_todo:
            return reverse("tournament_todo")
        return super(StaffLoginView, self).get_success_url()


def tournament_todo(request):
    if not request.user.is_authenticated:
        return redirect("tab_login")

    preference, _ = UserTournamentSetupPreference.objects.get_or_create(
        user=request.user
    )
    steps = get_tournament_todo_steps()

    if request.method == "POST":
        preference.hide_tournament_todo = parse_checkbox_value(
            request.POST.get("hide_tournament_todo")
        )
        preference.save(update_fields=["hide_tournament_todo"])

        for step in steps:
            step_field = f"step_{step['slug']}"
            if step["auto_completed"]:
                set_step_checked(step["slug"], True)
            else:
                set_step_checked(
                    step["slug"],
                    parse_checkbox_value(request.POST.get(step_field)),
                )

        return redirect_and_flash_success(
            request,
            "Tournament checklist updated.",
            path=reverse("tournament_todo"),
        )

    return render(
        request,
        "tab/tournament_todo.html",
        {
            "title": "Tournament Checklist",
            "sections": get_tournament_todo_sections(steps),
            "hide_tournament_todo": preference.hide_tournament_todo,
        },
    )


def tournament_todo_toggle(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=403)
    if request.method != "POST":
        return JsonResponse({"error": "Only POST is allowed."}, status=405)

    field_name = (request.POST.get("field") or "").strip()
    checked = parse_checkbox_value(request.POST.get("checked", ""))

    preference, _ = UserTournamentSetupPreference.objects.get_or_create(
        user=request.user
    )

    if field_name == "hide_tournament_todo":
        preference.hide_tournament_todo = checked
        preference.save(update_fields=["hide_tournament_todo"])
        return JsonResponse({
            "ok": True,
            "field": field_name,
            "checked": preference.hide_tournament_todo,
        })

    steps = get_tournament_todo_steps()
    step = get_field_to_step_map(steps).get(field_name)
    if step is None:
        return JsonResponse({"error": "Unknown checklist field."}, status=400)

    target_checked = checked
    if step["auto_completed"]:
        target_checked = True

    set_step_checked(step["slug"], target_checked)

    updated_steps = get_tournament_todo_steps()
    updated_step = get_field_to_step_map(updated_steps).get(field_name)
    if updated_step is None:
        return JsonResponse({
            "ok": True,
            "field": field_name,
            "checked": target_checked,
            "auto_completed": step["auto_completed"],
        })

    sections = get_tournament_todo_sections(updated_steps)
    section = next(
        (item for item in sections if item["phase"] == updated_step["phase"]),
        None,
    )

    response_data = {
        "ok": True,
        "field": field_name,
        "checked": updated_step["checked"],
        "auto_completed": updated_step["auto_completed"],
        "phase": updated_step["phase"],
    }
    if section is not None:
        response_data.update({
            "section_completed": section["completed"],
            "section_total": section["total"],
            "section_progress_percent": section["progress_percent"],
        })

    return JsonResponse(response_data)
