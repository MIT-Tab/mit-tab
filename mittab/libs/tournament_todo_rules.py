import os

import yaml
from django.conf import settings

from mittab.apps.tab.models import (
    CheckIn,
    Debater,
    DEFAULT_TOURNAMENT_NAME,
    Judge,
    Outround,
    Room,
    RoomCheckIn,
    RoomTag,
    Round,
    School,
    Scratch,
    TabSettings,
    Team,
)
from mittab.apps.tab.public_rankings import get_standings_publication_setting


SETTINGS_FORM_VISITED_CATEGORIES_KEY = "settings_tabs_seen"


def split_csv_values(raw_value):
    return {
        value.strip() for value in str(raw_value or "").split(",")
        if value.strip()
    }


def get_seen_settings_category_ids():
    return split_csv_values(
        TabSettings.get(SETTINGS_FORM_VISITED_CATEGORIES_KEY, "")
    )


def record_seen_settings_category_ids(category_ids):
    if not category_ids:
        return
    updated_ids = sorted(get_seen_settings_category_ids() | set(category_ids))
    TabSettings.set(SETTINGS_FORM_VISITED_CATEGORIES_KEY, ",".join(updated_ids))


def get_required_settings_category_ids():
    settings_dir = os.path.join(settings.BASE_DIR, "settings")
    required_ids = set()
    if not os.path.isdir(settings_dir):
        return required_ids

    for filename in sorted(os.listdir(settings_dir)):
        if not filename.lower().endswith((".yaml", ".yml")):
            continue

        yaml_file = os.path.join(settings_dir, filename)
        if not os.path.isfile(yaml_file):
            continue

        try:
            with open(yaml_file, "r", encoding="utf-8") as stream:
                data = yaml.safe_load(stream) or {}
        except (OSError, yaml.YAMLError):
            continue

        if not isinstance(data, dict):
            continue

        category_id = (data.get("category") or {}).get("id")
        if category_id:
            required_ids.add(category_id)

    return required_ids


def rule_tournament_name_set():
    tournament_name = (
        TabSettings.get("tournament_name", DEFAULT_TOURNAMENT_NAME) or ""
    ).strip()
    return bool(tournament_name) and tournament_name != DEFAULT_TOURNAMENT_NAME


def rule_settings_reviewed():
    required_ids = get_required_settings_category_ids()
    if not required_ids:
        return False
    return required_ids.issubset(get_seen_settings_category_ids())


def rule_core_data_imported():
    return all(model.objects.exists() for model in (School, Team, Debater, Room, Judge))


def rule_accessible_room_tags_added():
    return (
        RoomTag.objects.exists()
        and Room.objects.filter(tags__isnull=False).distinct().exists()
    )


def rule_scratches_entered():
    return Scratch.objects.exists()


def rule_entities_checked_in():
    return (
        Team.objects.filter(checked_in=True).exists()
        and CheckIn.objects.filter(round_number=1).exists()
        and RoomCheckIn.objects.filter(round_number=1).exists()
    )


def rule_round_one_paired():
    return Round.objects.filter(round_number=1).exists()


def rule_final_inround_completed():
    final_round_number = int(TabSettings.get("tot_rounds", 0) or 0)
    if final_round_number < 1:
        return False

    final_round_qs = Round.objects.filter(round_number=final_round_number)
    return (
        final_round_qs.exists()
        and not final_round_qs.filter(victor=Round.UNKNOWN).exists()
    )


def rule_speaker_results_published():
    return bool(get_standings_publication_setting("speaker_results")["published"])


def rule_team_results_published():
    return bool(get_standings_publication_setting("team_results")["published"])


def rule_outrounds_paired():
    return Outround.objects.exists()


def rule_outrounds_completed():
    return (
        Outround.objects.exists()
        and not Outround.objects.filter(victor=Outround.UNKNOWN).exists()
    )


TOURNAMENT_TODO_RULES = {
    "tournament_name_set": rule_tournament_name_set,
    "settings_reviewed": rule_settings_reviewed,
    "core_data_imported": rule_core_data_imported,
    "accessible_room_tags_added": rule_accessible_room_tags_added,
    "scratches_entered": rule_scratches_entered,
    "entities_checked_in": rule_entities_checked_in,
    "round_one_paired": rule_round_one_paired,
    "final_inround_completed": rule_final_inround_completed,
    "speaker_results_published": rule_speaker_results_published,
    "team_results_published": rule_team_results_published,
    "outrounds_paired": rule_outrounds_paired,
    "outrounds_completed": rule_outrounds_completed,
}
