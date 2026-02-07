"""
Views for managing and displaying debate motions.

This module provides both private (admin) views for tournament directors
to manage motions, and public views for competitors and judges to view
published motions.
"""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.http import JsonResponse
from django.utils.html import escape

from mittab.apps.tab.helpers import redirect_and_flash_error, redirect_and_flash_success
from mittab.apps.tab.models import Motion, TabSettings, BreakingTeam
from mittab.libs.cacheing.public_cache import cache_public_view, invalidate_public_motions_cache


logger = logging.getLogger(__name__)


def _get_available_rounds():
    """
    Get list of available rounds for motion entry.
    Returns a list of tuples: (round_type, round_id, round_display_name)
    where round_type is 'inround' or 'outround'.
    """
    rounds = []
    
    # Add inrounds
    tot_rounds = TabSettings.get("tot_rounds", 5)
    for i in range(1, tot_rounds + 1):
        rounds.append(('inround', i, f"Round {i}"))
    
    # Add outrounds - standard sizes
    outround_sizes = [64, 32, 16, 8, 4, 2]
    round_names = {
        2: "Finals",
        4: "Semifinals",
        8: "Quarterfinals",
        16: "Octofinals",
        32: "Double Octofinals",
        64: "Triple Octofinals",
    }
    
    for num_teams in outround_sizes:
        round_name = round_names.get(num_teams, f"Round of {num_teams}")
        # Varsity outrounds
        rounds.append(('outround', f"0_{num_teams}", f"Varsity {round_name}"))
        # Novice outrounds
        rounds.append(('outround', f"1_{num_teams}", f"Novice {round_name}"))
    
    return rounds


def _get_motions_for_display():
    """
    Get all motions organized for display, sorted appropriately.
    """
    motions = list(Motion.objects.all())
    motions.sort(key=lambda m: m.sort_key)
    return motions


def _get_published_motions():
    """
    Get only published motions for public display.
    Sorted in reverse order so the latest/highest round appears first.
    """
    motions = list(Motion.objects.filter(is_published=True))
    motions.sort(key=lambda m: m.sort_key, reverse=True)
    return motions


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def manage_motions(request):
    """
    Admin view for managing all motions.
    Tournament directors can add, edit, delete, and publish/unpublish motions.
    """
    motions_enabled = TabSettings.get("motions_enabled", 0)
    
    if not motions_enabled:
        return redirect_and_flash_error(
            request,
            "Motions feature is disabled. Enable it in Settings to use this feature.",
            path=reverse("settings_form")
        )
    
    motions = _get_motions_for_display()
    available_rounds = _get_available_rounds()
    
    return render(request, "motions/manage_motions.html", {
        "title": "Manage Motions",
        "motions": motions,
        "available_rounds": available_rounds,
    })


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def add_motion(request):
    """
    Add a new motion.
    """
    if request.method != "POST":
        return redirect("manage_motions")
    
    round_selection = request.POST.get("round_selection", "")
    info_slide = request.POST.get("info_slide", "").strip()
    motion_text = request.POST.get("motion_text", "").strip()
    
    if not motion_text:
        return redirect_and_flash_error(request, "Motion text is required.")
    
    if not round_selection:
        return redirect_and_flash_error(request, "Round selection is required.")
    
    # Parse round selection
    try:
        if round_selection.startswith("inround_"):
            round_number = int(round_selection.replace("inround_", ""))
            motion = Motion(
                round_number=round_number,
                info_slide=info_slide,
                motion_text=motion_text,
            )
        elif round_selection.startswith("outround_"):
            parts = round_selection.replace("outround_", "").split("_")
            outround_type = int(parts[0])
            num_teams = int(parts[1])
            motion = Motion(
                outround_type=outround_type,
                num_teams=num_teams,
                info_slide=info_slide,
                motion_text=motion_text,
            )
        else:
            return redirect_and_flash_error(request, "Invalid round selection.")
        
        motion.full_clean()
        motion.save()
        _invalidate_motions_cache()
        return redirect_and_flash_success(request, "Motion added successfully.", path=reverse("manage_motions"))
    except Exception as e:
        return redirect_and_flash_error(request, f"Error adding motion: {str(e)}")


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def edit_motion(request, motion_id):
    """
    Edit an existing motion.
    """
    motion = get_object_or_404(Motion, pk=motion_id)

    if request.method == "POST":
        round_selection = request.POST.get("round_selection", "")
        info_slide = request.POST.get("info_slide", "").strip()
        motion_text = request.POST.get("motion_text", "").strip()

        if not motion_text:
            messages.error(request, "Motion text is required.")
        elif not round_selection:
            messages.error(request, "Round selection is required.")
        else:
            try:
                if round_selection.startswith("inround_"):
                    round_number = int(round_selection.replace("inround_", ""))
                    motion.round_number = round_number
                    motion.outround_type = None
                    motion.num_teams = None
                elif round_selection.startswith("outround_"):
                    parts = round_selection.replace("outround_", "").split("_")
                    outround_type = int(parts[0])
                    num_teams = int(parts[1])
                    motion.round_number = None
                    motion.outround_type = outround_type
                    motion.num_teams = num_teams
                else:
                    raise ValidationError("Invalid round selection.")

                motion.info_slide = info_slide
                motion.motion_text = motion_text
                motion.full_clean()
                motion.save()
                _invalidate_motions_cache()
                return redirect_and_flash_success(request, "Motion updated successfully.", path=reverse("manage_motions"))
            except ValidationError as e:
                messages.error(request, " ".join(e.messages))
            except Exception:
                logger.exception("Unexpected error updating motion %s", motion_id)
                messages.error(request, "Unexpected error updating motion. Please try again.")

    available_rounds = _get_available_rounds()

    return render(request, "motions/edit_motion.html", {
        "title": f"Edit Motion - {motion.round_display}",
        "motion": motion,
        "available_rounds": available_rounds,
        "current_round_selection": request.POST.get("round_selection", motion.round_selection_value),
        "submitted_info_slide": request.POST.get("info_slide", motion.info_slide),
        "submitted_motion_text": request.POST.get("motion_text", motion.motion_text),
    })


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def delete_motion(request, motion_id):
    """
    Delete a motion.
    """
    if request.method != "POST":
        return redirect("manage_motions")
    
    motion = get_object_or_404(Motion, pk=motion_id)
    motion.delete()
    _invalidate_motions_cache()
    return redirect_and_flash_success(request, "Motion deleted successfully.", path=reverse("manage_motions"))


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def toggle_motion_published(request, motion_id):
    """
    Toggle the published status of a motion.
    """
    if request.method != "POST":
        return redirect("manage_motions")
    
    motion = get_object_or_404(Motion, pk=motion_id)
    motion.is_published = not motion.is_published
    motion.save()
    _invalidate_motions_cache()
    
    status = "published" if motion.is_published else "unpublished"
    return redirect_and_flash_success(
        request,
        f"Motion {status} successfully.",
        path=reverse("manage_motions")
    )


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def publish_all_motions(request):
    """
    Publish all motions at once.
    """
    if request.method != "POST":
        return redirect("manage_motions")
    
    Motion.objects.all().update(is_published=True)
    _invalidate_motions_cache()
    return redirect_and_flash_success(request, "All motions published.", path=reverse("manage_motions"))


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def unpublish_all_motions(request):
    """
    Unpublish all motions at once.
    """
    if request.method != "POST":
        return redirect("manage_motions")
    
    Motion.objects.all().update(is_published=False)
    _invalidate_motions_cache()
    return redirect_and_flash_success(request, "All motions unpublished.", path=reverse("manage_motions"))


@cache_public_view(timeout=60)
def public_motions(request):
    """
    Public view for competitors and judges to view published motions.
    
    Text is safely escaped to prevent XSS attacks.
    """
    motions_enabled = TabSettings.get("motions_enabled", 0)
    
    if not motions_enabled:
        return redirect("public_access_error")
    
    motions = _get_published_motions()
    
    # Group motions by type for display
    inround_motions = [m for m in motions if not m.is_outround]
    outround_motions = [m for m in motions if m.is_outround]
    
    # Further group outrounds by type
    varsity_outrounds = [m for m in outround_motions if m.outround_type == BreakingTeam.VARSITY]
    novice_outrounds = [m for m in outround_motions if m.outround_type == BreakingTeam.NOVICE]
    
    return render(request, "public/motions.html", {
        "title": "Motions",
        "inround_motions": inround_motions,
        "varsity_outrounds": varsity_outrounds,
        "novice_outrounds": novice_outrounds,
        "has_motions": bool(motions),
    })


def _invalidate_motions_cache():
    """Invalidate the public motions cache when motions are modified."""
    invalidate_public_motions_cache()
