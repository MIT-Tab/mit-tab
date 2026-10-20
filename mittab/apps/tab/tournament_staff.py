from mittab.apps.tab.models import TabSettings, TournamentStaffRole


CORE_TOURNAMENT_STAFF_FIELDS = (
    ("tournament_director", "Tournament Director"),
    ("tab_director", "Tab Director"),
    ("equity_officers", "Equity Officer(s)"),
    ("tournament_observer", "Tournament Observer"),
)


def split_staff_names(value):
    return [name.strip() for name in str(value or "").splitlines() if name.strip()]


def get_public_tournament_staff():
    staff = []
    for setting, title in CORE_TOURNAMENT_STAFF_FIELDS:
        names = split_staff_names(TabSettings.get(setting, ""))
        if names:
            staff.append({"title": title, "names": names})

    for role in TournamentStaffRole.objects.all():
        names = split_staff_names(role.names)
        if role.title.strip() and names:
            staff.append({"title": role.title.strip(), "names": names})
    return staff
