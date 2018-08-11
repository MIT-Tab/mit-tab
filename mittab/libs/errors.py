import os
import sys
import logging

from raven.contrib.django.raven_compat.models import client


__log = logging.getLogger(__name__)

def emit_current_exception():
    __log.exception("Got exception, caught it")
    if os.environ.get('DEBUG') not in ['1', 1, True, 'true']:
        client.captureException()


class ByeAssignmentError(Exception):
    pass

class NoShowAssignmentError(Exception):
    pass

class NotEnoughTeamsError(Exception):
    pass

class RoomAlreadyExistsError(Exception):
    pass

class JudgeAlreadyExistsError(Exception):
    pass

class SchoolAlreadyExistsError(Exception):
    pass

class DebaterAlreadyExistsError(Exception):
    pass

class TeamAlreadyExistsError(Exception):
    pass

class TeamDoesntExistError(Exception):
    pass

class RoomDoesntExistError(Exception):
    pass

class SchoolDoesntExistError(Exception):
    pass

class SchoolInUseError(Exception):
    pass

class DebaterDoesntExistError(Exception):
    pass

class JudgeDoesntExistError(Exception):
    pass

class NotEnoughJudgesError(Exception):
    pass

class NotEnoughRoomsError(Exception):
    pass

class DebaterOnMoreThanOneTeamError(Exception):
    def __init__(self, debaters):
        self.debaters = debaters
    def __str__(self):
        return repr(self.debaters)

class NeedTwoDifferentDebatersError(Exception):
    pass

class YouAreDumbError(Exception):
    def __init__(self):
        self.msg = "Really?  You don't have that many teams at your tournament"
    def __str__(self):
        return repr(self.msg)

class NotEnoughNoviceTeamsError(Exception):
    pass

class ToManyScratchesError(Exception):
    def __init__(self):
        self.msg = "Judge assignment impossible with current scratches"
    def __str__(self):
        return repr(self.msg)

class JudgeAssignmentError(Exception):
    def __init__(self, reason=None):
        if reason is not None:
            self.msg = reason
        else:
            self.msg = "Could not assign judges"

    def __str__(self):
        return repr(self.msg)

class PrevRoundNotEnteredError(Exception):
    def __init__(self):
        self.msg = "You have not entered all the results from the previous round."
    def __str__(self):
        return repr(self.msg)
