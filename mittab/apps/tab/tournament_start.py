from mittab.apps.registration.models import RegistrationConfig
from mittab.apps.tab.models import TabSettings


START_TOURNAMENT_WIZARD_COMPLETED = "start_tournament_wizard_completed"


def is_start_tournament_wizard_complete():
    """Return whether initial setup has been completed for this tournament.

    Tournaments created before the wizard existed do not have the setting and
    should not be interrupted in the middle of an event. New tournaments set
    the value to zero during initialization.
    """
    value = TabSettings.get(START_TOURNAMENT_WIZARD_COMPLETED, None)
    if value is None:
        return True
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def apply_new_tournament_defaults():
    """Require the start wizard and close registration for a new tournament."""
    TabSettings.set(START_TOURNAMENT_WIZARD_COMPLETED, 0)
    config = RegistrationConfig.get_or_create_active()
    config.allow_new_registrations = False
    config.save()
