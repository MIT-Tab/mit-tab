from types import SimpleNamespace

from django import forms
from django.contrib import admin
from django.contrib.admin.actions import delete_selected
from django.contrib.auth import get_user
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group, User
from django.contrib.admin.utils import unquote
from django.contrib.sessions.models import Session
from django.db import router, transaction
from django.db.models import Q, QuerySet
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters

from mittab.apps.tab import models
from mittab.apps.tab.admin_delete_scope import (
    account_deletion_records, account_deletion_scope, room_deletion_scope, pairing_cascade_deletion_scope,
)
from mittab.apps.tab.admin_pairing_deletion import LATER_STAGE, PairingCascade, later_stage_survives, repair_removed_outrounds
from mittab.apps.tab.auth_roles import APDA_BOARD_GROUP_NAME, is_apda_board_user
from mittab.libs.cacheing.cache_logic import refresh_rankings_after_commit
from mittab.libs.outround_tab_logic.pairing import prepare_challenge_edits
from mittab.apps.tab.live_results import lock_elimination_tournament
from mittab.apps.tab.board_audit import (
    pairing_snapshot, record_pairing_change, with_pairing_snapshot_relations,
)


class ProtectedAdminChange(Exception):
    """A safeguard rejected a change to a managed admin record."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class AdminAccessChanged(Exception):
    """The requesting session lost staff access while waiting to write."""


def _require_current_admin(request):
    session = type(request.session)(session_key=request.session.session_key)
    actor = get_user(SimpleNamespace(session=session))
    if (actor.pk != request.user.pk or not actor.is_active or not actor.is_staff
            or not actor.is_superuser or is_apda_board_user(actor)):
        raise AdminAccessChanged
    request.user = actor


def _lock_current_admin_session(request, database):
    if not User.objects.using(database).select_for_update().filter(pk=request.user.pk, is_superuser=True).exists():
        raise AdminAccessChanged
    sessions = Session.objects.using(database).select_for_update()
    if not sessions.filter(session_key=request.session.session_key, expire_date__gt=timezone.now()).exists():
        raise AdminAccessChanged
    _require_current_admin(request)


def _protected_deletion_response(error, status):
    return HttpResponse(str(error), status=error.status or status, content_type="text/plain; charset=utf-8",
                        headers={"Cache-Control": "private, no-store"})


class ProtectedDeletionMixin:
    blocked_status = 409
    related_permission_message = None

    def delete_view(self, request, object_id, extra_context=None):
        try:
            return super().delete_view(request, object_id, extra_context)
        except AdminAccessChanged:
            return redirect("/403/")
        except ProtectedAdminChange as error:
            return _protected_deletion_response(error, self.blocked_status)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if "delete_selected" in actions:
            _, name, label = actions["delete_selected"]
            actions["delete_selected"] = (type(self).delete_selected_safely, name, label)
        return actions

    def delete_selected_safely(self, request, queryset):
        try:
            if not request.POST.get("post"):
                return delete_selected(self, request, queryset)
            with transaction.atomic(using=router.db_for_write(self.model)):
                ids = self._lock_selection(queryset)
                _require_current_admin(request)
                return delete_selected(self, request, queryset.filter(pk__in=ids))
        except AdminAccessChanged:
            return redirect("/403/")
        except ProtectedAdminChange as error:
            return _protected_deletion_response(error, self.blocked_status)

    def _lock_selection(self, selected):
        return list(selected.order_by("pk").select_for_update().values_list("pk", flat=True))

    def get_deleted_objects(self, objs, request):
        ids = [obj.pk for obj in objs]
        database = router.db_for_write(self.model)
        selected = self.model._base_manager.using(database).filter(pk__in=ids)
        # Django passes a sequence to the single-record view and a queryset to its action.
        bulk = isinstance(objs, QuerySet)
        confirmed = bool(request.POST) and (not bulk or bool(request.POST.get("post")))
        if confirmed and not bulk:
            self._lock_selection(selected)
            _require_current_admin(request)
        reason = self.deletion_rejection(selected, request)
        if not reason:
            self.prepare_deletion_scope(request, selected, confirmed)
        deleted, counts, permissions, protected = super().get_deleted_objects(objs, request)
        if confirmed and permissions and self.related_permission_message:
            raise ProtectedAdminChange(self.related_permission_message, status=403)
        if reason:
            if confirmed:
                raise ProtectedAdminChange(reason)
            protected.append(reason)
        if not (permissions or protected):
            self.review_deletion_scope(request, selected, deleted, counts, confirmed, protected)
        return deleted, counts, permissions, protected

    def prepare_deletion_scope(self, request, selected, confirmed):
        """Lock any required dependencies before Django collects the deletion preview."""

    def review_deletion_scope(self, request, selected, deleted, counts, confirmed, protected):
        """Allow a model to require confirmation of the dependencies it showed."""


class SignedDeletionMixin(ProtectedDeletionMixin):
    def review_deletion_scope(self, request, selected, deleted, counts, confirmed, protected):
        if not deleted:
            return
        scope = self.deletion_scope(request, selected, deleted, counts, confirmed)
        request.admin_deletion_scope = scope
        if confirmed and not constant_time_compare(scope, request.POST.get("mittab_delete_scope", "")):
            request.admin_deletion_scope_changed = True
            # Stop Django's stock delete handling. The refreshed preview replaces this stop flag.
            protected.append("Review updated deletion")

    @staticmethod
    def _show_deletion_scope(request, context):
        context["delete_scope"] = getattr(request, "admin_deletion_scope", None)
        stale = getattr(request, "admin_deletion_scope_changed", False)
        if stale:
            context.update(title="Review updated deletion", protected=[])
        return stale

    def render_delete_form(self, request, context):
        stale = self._show_deletion_scope(request, context)
        response = super().render_delete_form(request, context)
        if stale:
            response.status_code = 409
        return response

    def _apply_bulk_confirmation(self, request, queryset):
        if request.POST.get("post") in (None, "", "yes"):
            return super().delete_selected_safely(request, queryset)
        submitted = request.POST
        request.POST = submitted.copy()
        request.POST.pop("post")
        try:
            return super().delete_selected_safely(request, queryset)
        finally:
            request.POST = submitted

    def delete_selected_safely(self, request, queryset):
        response = self._apply_bulk_confirmation(request, queryset)
        if hasattr(response, "context_data") and self._show_deletion_scope(request, response.context_data):
            response.status_code = 409
        return response


class SchoolAdmin(ProtectedDeletionMixin, admin.ModelAdmin):
    def deletion_rejection(self, selected, request):
        if (models.Team.objects.using(selected.db).filter(school__in=selected).exists()
                or models.Judge.objects.using(selected.db).filter(schools__in=selected).exists()
                or selected.filter(registration__isnull=False).exists()):
            return "This school has teams, affiliated judges, or registrations."
        return None


class DebaterAdmin(ProtectedDeletionMixin, admin.ModelAdmin):
    def deletion_rejection(self, selected, request):
        if models.Team.objects.using(selected.db).filter(debaters__in=selected).exists():
            return "Remove this debater from their team before deleting the debater."
        return None


class RoomAdmin(SignedDeletionMixin, admin.ModelAdmin):
    deletion_scope = staticmethod(room_deletion_scope)

    def _lock_selection(self, selected):
        # Coordinate room deletion with staff and Board elimination writes.
        tournament = lock_elimination_tournament(using=selected.db)
        ids = super()._lock_selection(selected)
        pairings = list(models.Outround.objects.using(selected.db).select_for_update()
                        .filter(room_id__in=ids).order_by("pk").values_list("pk", flat=True))
        if tournament is None and pairings:
            raise ProtectedAdminChange("Unable to complete this request.", status=503)
        return ids

    def deletion_rejection(self, selected, request):
        if models.Round.objects.using(selected.db).filter(room__in=selected).exists():
            return "This room is assigned to a round."
        removed = models.Outround.objects.using(selected.db).filter(room__in=selected)
        if later_stage_survives(removed):
            return LATER_STAGE
        return None

    @staticmethod
    def _pairings(rooms):
        return [(pairing, pairing_snapshot(pairing)) for pairing in with_pairing_snapshot_relations(
            models.Outround.objects.filter(room__in=rooms),
        )]

    def delete_model(self, request, obj):
        pairings = self._pairings([obj])
        super().delete_model(request, obj)
        repair_removed_outrounds(request.user, pairings)

    def delete_queryset(self, request, queryset):
        pairings = self._pairings(queryset)
        super().delete_queryset(request, queryset)
        repair_removed_outrounds(request.user, pairings)


class AccountDeletionMixin(SignedDeletionMixin):
    blocked_status = 422
    deletion_scope = staticmethod(account_deletion_scope)

    @staticmethod
    def _lock_administrators(database):
        list(User.objects.using(database).filter(is_superuser=True)
             .order_by("pk").select_for_update().values_list("pk", flat=True))

    def _lock_selection(self, selected):
        self._lock_administrators(selected.db)
        return super()._lock_selection(selected)

    def delete_view(self, request, object_id, extra_context=None):
        if request.method != "POST":
            return super().delete_view(request, object_id, extra_context)
        if request.POST.get("post") != "yes" and request.POST.get("confirm", "").lower() not in {"true", "on", "1"}:
            return _protected_deletion_response(ProtectedAdminChange("Confirm deletion before continuing."), 422)
        database = router.db_for_write(self.model)
        with transaction.atomic(using=database):
            self._lock_administrators(database)
            return super().delete_view(request, object_id, extra_context)

    def prepare_deletion_scope(self, request, selected, confirmed):
        request.admin_account_deletion_records = account_deletion_records(selected, confirmed)

    def review_deletion_scope(self, request, selected, deleted, counts, confirmed, protected):
        super().review_deletion_scope(request, selected, deleted, counts, confirmed, protected)
        if confirmed:
            _lock_current_admin_session(request, selected.db)


class TournamentUserAdmin(AccountDeletionMixin, UserAdmin):
    def _edit_existing_user(self, request, object_id, edit):
        database = router.db_for_write(self.model)
        try:
            with transaction.atomic(using=database):
                self._lock_administrators(database)
                target = self.get_object(request, unquote(object_id))
                if target is None or not User.objects.using(database).select_for_update().filter(pk=target.pk).exists():
                    raise ProtectedAdminChange("Account or group not found.", status=404)
                _require_current_admin(request)
                return edit()
        except AdminAccessChanged:
            return redirect("/403/")
        except ProtectedAdminChange as error:
            return _protected_deletion_response(error, self.blocked_status)

    @method_decorator(sensitive_post_parameters())
    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        edit = super().changeform_view
        if request.method == "POST" and object_id is not None:
            return self._edit_existing_user(request, object_id, lambda: edit(request, object_id, form_url, extra_context))
        return edit(request, object_id, form_url, extra_context)

    @method_decorator(sensitive_post_parameters())
    def user_change_password(self, request, id, form_url=""):
        edit = super().user_change_password
        if request.method == "POST":
            return self._edit_existing_user(request, id, lambda: edit(request, id, form_url))
        return edit(request, id, form_url)

    def deletion_rejection(self, selected, request):
        if selected.filter(pk=request.user.pk).exists():
            return "Use a different administrator account to delete this user."
        return None


class TournamentGroupAdmin(AccountDeletionMixin, GroupAdmin):

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        try:
            return super().changeform_view(request, object_id, form_url, extra_context)
        except AdminAccessChanged:
            return redirect("/403/")
        except ProtectedAdminChange as error:
            return _protected_deletion_response(error, self.blocked_status)

    def save_model(self, request, obj, form, change):
        if change:
            groups = Group.objects.using(router.db_for_write(Group))
            selected = groups.filter(pk=obj.pk)
            if not self._lock_selection(selected):
                raise ProtectedAdminChange("This group changed. Reload it before saving.", status=409)
            _require_current_admin(request)
            previous_name = selected.values_list("name", flat=True).get()
            if previous_name != obj.name and self.deletion_rejection(selected, request):
                raise ProtectedAdminChange("This group’s name is used by MIT-Tab’s access rules.")
        super().save_model(request, obj, form, change)

    def deletion_rejection(self, selected, request):
        if selected.filter(Q(name__iexact=APDA_BOARD_GROUP_NAME) |
                           Q(name__istartswith="MIT-TAB: ")).exists():
            return "This group is used by MIT-Tab’s access rules."
        return None


class PairingCascadeDeletionMixin(SignedDeletionMixin):
    deletion_scope = staticmethod(pairing_cascade_deletion_scope)
    related_permission_message = "You do not have permission to delete related records."

    def _lock_selection(self, selected):
        tournament = lock_elimination_tournament(using=selected.db)
        ids = super()._lock_selection(selected)
        if tournament is None and self._affected_outrounds(selected.filter(pk__in=ids)).exists():
            raise ProtectedAdminChange("Unable to complete this request.", status=503)
        return ids

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if request.method != "POST" or object_id is None:
            return super().changeform_view(request, object_id, form_url, extra_context)
        database = router.db_for_write(self.model)
        try:
            with transaction.atomic(using=database):
                lock_elimination_tournament(using=database)
                target = self.get_object(request, unquote(object_id))
                if target is None or not self.model.objects.using(database).select_for_update().filter(pk=target.pk).exists():
                    return redirect("/admin/")
                _lock_current_admin_session(request, database)
                return super().changeform_view(request, object_id, form_url, extra_context)
        except AdminAccessChanged:
            return redirect("/403/")

    def delete_view(self, request, object_id, extra_context=None):
        if request.method == "POST" and request.POST.get("post") != "yes":
            return _protected_deletion_response(ProtectedAdminChange("Confirm the deletion first."), 400)
        return super().delete_view(request, object_id, extra_context)

    def deletion_rejection(self, selected, request):
        removed = self._affected_outrounds(selected, include_panel=False)
        return LATER_STAGE if later_stage_survives(removed) else None

    def prepare_deletion_scope(self, request, selected, confirmed):
        request.admin_pairing_deletion = PairingCascade.capture(selected, confirmed)
        if confirmed:
            _lock_current_admin_session(request, selected.db)

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        request.admin_pairing_deletion.finish(request.user)

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset)
        request.admin_pairing_deletion.finish(request.user)


class DirectPairingDeletionMixin(PairingCascadeDeletionMixin):
    def _affected_outrounds(self, selected, *, include_panel=True):
        rounds = models.Outround.objects.using(selected.db)
        return rounds.filter(pk__in=selected) if self.model is models.Outround else rounds.none()


class PairingAuditAdminMixin:
    def save_model(self, request, obj, form, change):
        previous = type(obj).objects.filter(pk=obj.pk).first() if change else None
        form.pairing_before = pairing_snapshot(previous) if previous else None
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        record_pairing_change(form.instance, form.pairing_before, request.user, "admin correction")
        if isinstance(form.instance, models.Round):
            refresh_rankings_after_commit(using=form.instance._state.db)

class RoundAdminForm(forms.ModelForm):
    chair = forms.ModelChoiceField(queryset=models.Judge.objects.all())

    class Meta:
        model = models.Round
        fields = "__all__"


class OutroundAdminForm(forms.ModelForm):
    chair = forms.ModelChoiceField(queryset=models.Judge.objects.all())

    class Meta:
        model = models.Outround
        fields = "__all__"


class RoundAdmin(DirectPairingDeletionMixin, PairingAuditAdminMixin, admin.ModelAdmin):
    form = RoundAdminForm
    filter_horizontal = ("judges", )


class OutroundAdmin(DirectPairingDeletionMixin, PairingAuditAdminMixin, admin.ModelAdmin):
    form = OutroundAdminForm
    filter_horizontal = ("judges", )

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        try:
            return super().changeform_view(request, object_id, form_url, extra_context)
        except AdminAccessChanged:
            return redirect("/403/")
        except ProtectedAdminChange as error:
            return _protected_deletion_response(error, 409)

    def save_model(self, request, obj, form, change):
        database = router.db_for_write(self.model)
        if lock_elimination_tournament(using=database) is None:
            raise ProtectedAdminChange("Unable to complete this request.", status=503)
        _lock_current_admin_session(request, database)
        if obj.room_id and not models.Room.objects.using(database).filter(pk=obj.room_id).exists():
            raise ProtectedAdminChange("This room changed. Reload the form before saving.")
        super().save_model(request, obj, form, change)


class RankingInputAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        refresh_rankings_after_commit(using=obj._state.db)

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        refresh_rankings_after_commit(using=obj._state.db)

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset)
        refresh_rankings_after_commit(using=queryset.db)


class RoundStatsAdmin(RankingInputAdmin):
    def save_model(self, request, obj, form, change):
        round_ids = {obj.round_id}
        if change:
            old_id = models.RoundStats.objects.filter(pk=obj.pk).values_list(
                "round_id", flat=True).first()
            round_ids.add(old_id)
        rounds = list(models.Round.objects.filter(pk__in=round_ids))
        before = {pairing.pk: pairing_snapshot(pairing) for pairing in rounds}
        super().save_model(request, obj, form, change)
        for pairing in rounds:
            record_pairing_change(pairing, before[pairing.pk], request.user, "admin scores")

    def delete_model(self, request, obj):
        pairing = obj.round
        before = pairing_snapshot(pairing)
        super().delete_model(request, obj)
        record_pairing_change(pairing, before, request.user, "admin scores")

    def delete_queryset(self, request, queryset):
        rounds = list(models.Round.objects.filter(pk__in=queryset.values("round_id")))
        before = {pairing.pk: pairing_snapshot(pairing) for pairing in rounds}
        super().delete_queryset(request, queryset)
        for pairing in rounds:
            record_pairing_change(pairing, before[pairing.pk], request.user, "admin scores")


class AttributionAdminMixin:
    readonly_fields = ("created_by", "created_at")

    def save_model(self, request, obj, form, change):
        if not change and hasattr(obj, "created_by") and not obj.created_by_id:
            obj.created_by = request.user
        before_dino = (models.Judge.objects.filter(pk=obj.pk).values("is_dino").first()
                       if isinstance(obj, models.Judge) and change else None)
        super(AttributionAdminMixin, self).save_model(request, obj, form, change)
        event_type = models.AuditEvent.EDIT if change else models.AuditEvent.CREATE
        changes = {"fields": list(form.changed_data)} if change else {}
        if before_dino and before_dino["is_dino"] != obj.is_dino:
            changes["is_dino"] = {"before": before_dino["is_dino"], "after": obj.is_dino}
        models.AuditEvent.record(obj, event_type, request.user, changes=changes)


class RosterDeletionMixin(PairingCascadeDeletionMixin):
    def _affected_outrounds(self, selected, *, include_panel=True):
        rows = models.Outround.objects.using(selected.db)
        if self.model is models.Team:
            return rows.filter(Q(gov_team__in=selected) | Q(opp_team__in=selected))
        links = Q(chair__in=selected)
        if include_panel:
            links |= Q(judges__in=selected)
        return rows.filter(links)


class TeamAdmin(RosterDeletionMixin, AttributionAdminMixin, admin.ModelAdmin):
    exclude = ("team_code",)
    filter_horizontal = ("debaters", )


class JudgeAdmin(RosterDeletionMixin, AttributionAdminMixin, admin.ModelAdmin):
    filter_horizontal = ("schools", "required_room_tags")


class ScratchAdmin(AttributionAdminMixin, admin.ModelAdmin):
    pass


class ManualJudgeAssignmentAdmin(admin.ModelAdmin):
    readonly_fields = ("round", "judge", "created_by", "created_at")
    list_display = ("round", "judge", "created_by", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class AuditEventAdmin(admin.ModelAdmin):
    readonly_fields = (
        "content_type",
        "object_id",
        "event_type",
        "user",
        "created_at",
        "object_repr",
        "changes",
        "note",
    )
    list_display = ("created_at", "event_type", "object_repr", "user")
    list_filter = ("event_type", "content_type")
    search_fields = ("object_repr", "note")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class MotionAdmin(admin.ModelAdmin):
    list_display = ("round_display", "motion_text_truncated", "is_published", "updated_at")
    list_filter = ("is_published", "outround_type")
    search_fields = ("motion_text", "info_slide")
    ordering = ("round_number", "outround_type", "-num_teams")

    def motion_text_truncated(self, obj):
        return obj.motion_text[:100] + "..." if len(obj.motion_text) > 100 else obj.motion_text
    motion_text_truncated.short_description = "Motion Text"


class ExperimentalAdmin(admin.ModelAdmin):
    setting_key = None

    def enabled(self):
        return models.TabSettings.get(self.setting_key, 0) in self.enabled_values

    enabled_values = (1,)

    def get_model_perms(self, request):
        return super().get_model_perms(request) if self.enabled() else {}

    def has_module_permission(self, request):
        return self.enabled() and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return self.enabled() and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return self.enabled() and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.enabled() and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self.enabled() and super().has_delete_permission(request, obj)


class OutroundChallengeAdmin(ExperimentalAdmin):
    setting_key = "outround_pairing_mode"
    enabled_values = (1, 2)

    def save_model(self, request, obj, form, change):
        list(models.BreakingTeam.objects.select_for_update().filter(
            type_of_team=obj.type_of_round,
        ))
        prepare_challenge_edits(obj.type_of_round)
        super().save_model(request, obj, form, change)


admin.site.register(models.Debater, DebaterAdmin)
admin.site.register(models.Team, TeamAdmin)
admin.site.register(models.School, SchoolAdmin)
admin.site.register(models.Judge, JudgeAdmin)
admin.site.register(models.JudgeCodeEmailLog)
admin.site.register(models.WrittenRFDEmailLog)
admin.site.register(models.Scratch, ScratchAdmin)
admin.site.register(models.Round, RoundAdmin)
admin.site.register(models.RoundStats, RoundStatsAdmin)
admin.site.register(models.CheckIn)
admin.site.register(models.RoomCheckIn)
admin.site.register(models.TabSettings)
admin.site.register(models.UserTournamentSetupPreference)
admin.site.register(models.Room, RoomAdmin)
admin.site.register(models.RoomTag)
admin.site.register(models.RankingGroup)
admin.site.register(models.Bye, RankingInputAdmin)
admin.site.register(models.NoShow, RankingInputAdmin)
admin.site.register(models.BreakingTeam)
admin.site.register(models.Outround, OutroundAdmin)
admin.site.register(models.OutroundChallenge, OutroundChallengeAdmin)
admin.site.register(models.Motion, MotionAdmin)
admin.site.register(models.ManualJudgeAssignment, ManualJudgeAssignmentAdmin)
admin.site.register(models.AuditEvent, AuditEventAdmin)

admin.site.unregister(Group)
admin.site.register(Group, TournamentGroupAdmin)

admin.site.unregister(User)
admin.site.register(User, TournamentUserAdmin)
