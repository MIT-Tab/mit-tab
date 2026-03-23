from mittab.apps.tab.models import Round, TabSettings

APDA_BOARD_GROUP_NAME = "APDA Board"


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
