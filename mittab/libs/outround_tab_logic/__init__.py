from mittab.libs.outround_tab_logic.pairing import pair, perform_the_break
from mittab.libs.outround_tab_logic.checks import have_enough_judges, \
    have_enough_rooms, have_properly_entered_data, have_enough_judges_type, \
    have_enough_rooms_type, have_enough_rooms_before_break


def offset_to_quotient(offset):
    return 2 ** offset
