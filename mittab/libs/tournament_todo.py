import os

import yaml
from django.conf import settings
from django.urls import NoReverseMatch, reverse

from mittab.apps.tab.models import TabSettings
from mittab.libs.tournament_todo_rules import TOURNAMENT_TODO_RULES


TOURNAMENT_TODO_CONFIG_PATH = os.path.join(
    settings.BASE_DIR,
    "mittab",
    "apps",
    "tab",
    "config",
    "tournament_todo_steps.yaml",
)

TOURNAMENT_TODO_SECTION_SPECS = (
    ("before_tournament", "Before Tournament"),
    ("day_of", "Day Of Tournament"),
)


def get_step_tabsetting_key(step_slug):
    return f"todo_{step_slug}"


def is_step_checked(step_slug):
    return bool(TabSettings.get(get_step_tabsetting_key(step_slug), 0))


def set_step_checked(step_slug, is_checked):
    TabSettings.set(get_step_tabsetting_key(step_slug), 1 if is_checked else 0)


def get_step_field_name(step_slug):
    return f"step_{step_slug}"


def get_field_to_step_map(steps):
    return {get_step_field_name(step["slug"]): step for step in steps}


def get_tournament_todo_steps():
    if not os.path.exists(TOURNAMENT_TODO_CONFIG_PATH):
        return []

    try:
        with open(TOURNAMENT_TODO_CONFIG_PATH, "r", encoding="utf-8") as stream:
            config = yaml.safe_load(stream) or {}
    except (OSError, yaml.YAMLError):
        return []

    if not isinstance(config, dict):
        return []

    phase_keys = (
        ("before_tournament", "before_tournament_steps"),
        ("day_of", "day_of_steps"),
    )

    configured_steps = []
    for phase, key in phase_keys:
        for raw_step in config.get(key, []):
            if isinstance(raw_step, dict):
                configured_steps.append((phase, raw_step))

    if not configured_steps:
        for raw_step in config.get("steps", []):
            if not isinstance(raw_step, dict):
                continue
            phase = raw_step.get("phase", "before_tournament")
            configured_steps.append((phase, raw_step))

    steps = []
    for phase, raw_step in configured_steps:
        step_slug = raw_step.get("slug")
        if not step_slug:
            continue

        completion_rule = raw_step.get("completion_rule")
        completion_check = TOURNAMENT_TODO_RULES.get(completion_rule)
        auto_completed = bool(completion_check()) if completion_check else False

        checked = is_step_checked(step_slug)
        if auto_completed and not checked:
            set_step_checked(step_slug, True)
            checked = True

        step_url = raw_step.get("url_path", "")
        if not step_url and raw_step.get("url_name"):
            try:
                step_url = reverse(raw_step["url_name"])
            except NoReverseMatch:
                step_url = ""

        steps.append({
            "slug": step_slug,
            "title": raw_step.get("title", step_slug),
            "description": raw_step.get("description", ""),
            "url": step_url,
            "checked": checked,
            "auto_completed": auto_completed,
            "phase": phase,
        })

    return steps


def get_tournament_todo_sections(steps):
    sections = []
    for phase, section_title in TOURNAMENT_TODO_SECTION_SPECS:
        section_steps = [step for step in steps if step["phase"] == phase]
        completed = sum(1 for step in section_steps if step["checked"])
        total = len(section_steps)
        progress_percent = int((completed / total) * 100) if total else 0
        sections.append({
            "phase": phase,
            "title": section_title,
            "steps": section_steps,
            "completed": completed,
            "total": total,
            "progress_percent": progress_percent,
        })
    return sections
