from urllib.parse import urlsplit

from django.contrib.auth.models import Group
from django.urls import Resolver404, resolve, reverse

from mittab.apps.tab.models import Round, TabSettings

APDA_BOARD_GROUP_NAME = "APDA Board"

PRESET_FULL_TAB_STAFF = "full_tab_staff"
PRESET_ADD_SCRATCHES = "add_scratches"
PRESET_MANAGE_SCRATCHES = "manage_scratches"
PRESET_CHECKIN_HELPER = "checkin_helper"
PRESET_DATA_ENTRY_HELPER = "data_entry_helper"
PRESET_TAB_ASSISTANT = "tab_assistant"

CAP_ADD_SCRATCHES = "add_scratches"
CAP_VIEW_SCRATCHES = "view_scratches"
CAP_CHECKINS = "checkins"
CAP_DATA_ENTRY = "data_entry"

STAFF_PERMISSION_PRESETS = {
    PRESET_FULL_TAB_STAFF: {
        "label": "Full power tab staff",
        "summary": "Unrestricted tab staff access.",
        "can": [
            "Access all tab staff pages and workflows.",
            "Invite staff and manage permission presets.",
            "Change settings, run pairings, manage scratches, and use admin tools.",
        ],
        "cannot": [],
        "group": None,
        "capabilities": set(),
    },
    PRESET_ADD_SCRATCHES: {
        "label": "Add scratches",
        "summary": "One-purpose scratch entry role.",
        "can": ["Open the Add Scratch page and submit new scratches."],
        "cannot": [
            "View existing scratches.",
            "See whether a submitted scratch already exists.",
            "Access other tab staff pages.",
        ],
        "group": "MIT-TAB: Add scratches",
        "capabilities": {CAP_ADD_SCRATCHES},
    },
    PRESET_MANAGE_SCRATCHES: {
        "label": "Create and view scratches",
        "summary": "Scratch entry plus read-only scratch review.",
        "can": [
            "Add new scratches.",
            "View global, team, and judge scratch lists.",
        ],
        "cannot": [
            "Modify or delete existing scratches.",
            "Access data entry, check-in, settings, or pairing pages.",
        ],
        "group": "MIT-TAB: Create and view scratches",
        "capabilities": {CAP_ADD_SCRATCHES, CAP_VIEW_SCRATCHES},
    },
    PRESET_CHECKIN_HELPER: {
        "label": "Check-in helper",
        "summary": "Check-in desk role.",
        "can": [
            "View the batch check-in page.",
            "Check in or undo check-ins for teams, judges, and rooms.",
        ],
        "cannot": [
            "Create or edit tournament data.",
            "View scratches, settings, pairings, or email management.",
        ],
        "group": "MIT-TAB: Check-in helper",
        "capabilities": {CAP_CHECKINS},
    },
    PRESET_DATA_ENTRY_HELPER: {
        "label": "Data entry helper",
        "summary": "Roster and room data maintenance role.",
        "can": [
            "View, create, edit, and delete debaters, teams, schools, judges, and rooms.",
            "Bulk import teams, judges, and rooms.",
        ],
        "cannot": [
            "View or modify scratches.",
            "Change check-ins, settings, pairings, rankings, backups, or email management.",
        ],
        "group": "MIT-TAB: Data entry helper",
        "capabilities": {CAP_DATA_ENTRY},
    },
    PRESET_TAB_ASSISTANT: {
        "label": "Tab assistant",
        "summary": "Data entry plus check-in support.",
        "can": [
            "Do everything in Check-in helper.",
            "Do everything in Data entry helper.",
        ],
        "cannot": [
            "View or modify scratches.",
            "Change settings, pairings, rankings, backups, or email management.",
        ],
        "group": "MIT-TAB: Tab assistant",
        "capabilities": {CAP_CHECKINS, CAP_DATA_ENTRY},
    },
}

STAFF_PRESET_CHOICES = [
    (preset, config["label"])
    for preset, config in STAFF_PERMISSION_PRESETS.items()
]

STAFF_PRESET_GROUP_NAMES = {
    config["group"]
    for config in STAFF_PERMISSION_PRESETS.values()
    if config["group"]
}

STAFF_PRESET_DETAILS = [
    {
        "value": preset,
        "label": config["label"],
        "summary": config["summary"],
        "can": config["can"],
        "cannot": config["cannot"],
    }
    for preset, config in STAFF_PERMISSION_PRESETS.items()
]

CAPABILITY_ALLOWED_VIEWS = {
    CAP_ADD_SCRATCHES: {
        "add_scratch": {"GET", "POST"},
    },
    CAP_VIEW_SCRATCHES: {
        "add_scratches": {"GET", "POST"},
        "add_scratch": {"GET", "POST"},
        "view_scratches": {"GET"},
        "view_scratches_team": {"GET"},
    },
    CAP_CHECKINS: {
        "batch_checkin": {"GET"},
        "bulk_check_in": {"POST"},
    },
    CAP_DATA_ENTRY: {
        "delete_school": {"GET", "POST"},
        "delete_debater": {"GET", "POST"},
        "delete_judge": {"GET", "POST"},
        "delete_room": {"GET", "POST"},
        "delete_team": {"GET", "POST"},
        "enter_debater": {"GET", "POST"},
        "enter_judge": {"GET", "POST"},
        "enter_room": {"GET", "POST"},
        "enter_school": {"GET", "POST"},
        "enter_team": {"GET", "POST"},
        "index": {"GET"},
        "upload_data": {"GET", "POST"},
        "view_debater": {"GET", "POST"},
        "view_debaters": {"GET"},
        "view_judge": {"GET", "POST"},
        "view_judges": {"GET"},
        "view_room": {"GET", "POST"},
        "view_rooms": {"GET"},
        "view_school": {"GET", "POST"},
        "view_schools": {"GET"},
        "view_team": {"GET", "POST"},
        "view_teams": {"GET"},
    },
}

COMMON_RESTRICTED_STAFF_VIEWS = {
    "403",
    "404",
    "500",
    "admin_logout",
    "favicon",
    "logout",
    "staff_invite_complete",
    "tournament_logo",
}

STAFF_PRESET_LANDING_VIEWS = {
    PRESET_ADD_SCRATCHES: "add_scratch",
    PRESET_MANAGE_SCRATCHES: "view_scratches",
    PRESET_CHECKIN_HELPER: "batch_checkin",
    PRESET_DATA_ENTRY_HELPER: "index",
    PRESET_TAB_ASSISTANT: "index",
}


def is_apda_board_user(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return False
    return user.groups.filter(name=APDA_BOARD_GROUP_NAME).exists()


def is_apda_board_access_open():
    """APDA board access opens once the final inround has been paired."""
    try:
        total_inrounds = int(TabSettings.get("tot_rounds"))
    except (TypeError, ValueError):
        return False

    if total_inrounds < 1:
        return False
    return Round.objects.filter(round_number=total_inrounds).exists()


def staff_preset_for_user(user):
    if not user or not user.is_authenticated:
        return None
    if user.is_superuser:
        return PRESET_FULL_TAB_STAFF
    if not getattr(user, "is_staff", False):
        return None
    group_names = set(user.groups.values_list("name", flat=True))
    for preset, config in STAFF_PERMISSION_PRESETS.items():
        group_name = config["group"]
        if group_name and group_name in group_names:
            return preset
    return None


def staff_capabilities_for_user(user):
    preset = staff_preset_for_user(user)
    if not preset:
        return set()
    return set(STAFF_PERMISSION_PRESETS[preset]["capabilities"])


def user_has_staff_capability(user, capability):
    if user and user.is_authenticated and user.is_superuser:
        return True
    return capability in staff_capabilities_for_user(user)


def is_restricted_staff_user(user):
    return (
        user
        and user.is_authenticated
        and getattr(user, "is_staff", False)
        and not user.is_superuser
    )


def restricted_staff_landing_url(user):
    preset = staff_preset_for_user(user)
    if not preset:
        return reverse("403")
    view_name = STAFF_PRESET_LANDING_VIEWS.get(preset, "index")
    return reverse(view_name)


def restricted_staff_can_access_view(user, view_name, method):
    if not is_restricted_staff_user(user):
        return True
    if view_name in COMMON_RESTRICTED_STAFF_VIEWS:
        return True

    allowed_methods = set()
    for capability in staff_capabilities_for_user(user):
        allowed_methods.update(
            CAPABILITY_ALLOWED_VIEWS.get(capability, {}).get(view_name, set())
        )
    return method in allowed_methods


def restricted_staff_can_access_path(user, path, method="GET"):
    if not is_restricted_staff_user(user):
        return True

    resolved_path = urlsplit(path).path or "/"
    if resolved_path.startswith(("/static/", "/dynamic-media/")):
        return True

    try:
        view_name = resolve(resolved_path).view_name
    except Resolver404:
        return False
    return restricted_staff_can_access_view(user, view_name, method)


def apply_staff_permission_preset(user, preset):
    if preset not in STAFF_PERMISSION_PRESETS:
        raise ValueError(f"Unknown staff permission preset: {preset}")

    user.is_staff = True
    user.is_active = True
    user.is_superuser = preset == PRESET_FULL_TAB_STAFF
    user.save(update_fields=["is_staff", "is_active", "is_superuser"])

    user.groups.remove(*Group.objects.filter(name__in=STAFF_PRESET_GROUP_NAMES))
    group_name = STAFF_PERMISSION_PRESETS[preset]["group"]
    if group_name:
        group, _created = Group.objects.get_or_create(name=group_name)
        user.groups.add(group)
    return user
