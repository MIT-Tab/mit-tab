APDA_BOARD_GROUP_NAME = "APDA Board"


def is_apda_board_user(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return False
    return user.groups.filter(name=APDA_BOARD_GROUP_NAME).exists()
