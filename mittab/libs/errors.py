import os
import sys
import traceback

import sentry_sdk


def emit_current_exception():
    if os.environ.get("DEBUG") in ["1", 1, True, "true"]:
        traceback.print_exc(file=sys.stdout)
    else:
        sentry_sdk.capture_exception()


class ByeAssignmentError(Exception):
    pass


class NoShowAssignmentError(Exception):
    pass


class NotEnoughTeamsError(Exception):
    pass


class NotEnoughJudgesError(Exception):
    pass


class NotEnoughRoomsError(Exception):
    pass


class BadBreak(Exception):
    pass


class JudgeAssignmentError(Exception):
    def __init__(self, reason=None):
        super(JudgeAssignmentError, self).__init__()
        if reason is not None:
            self.msg = reason
        else:
            self.msg = "Could not assign judges"

    def __str__(self):
        return repr(self.msg)


class PrevRoundNotEnteredError(Exception):
    def __init__(self):
        super(PrevRoundNotEnteredError, self).__init__()
        self.msg = "You have not entered all the results from the previous round."

    def __str__(self):
        return repr(self.msg)


class RoomAssignmentError(Exception):
    def __init__(self, reason=None):
        super(RoomAssignmentError, self).__init__()
        if reason is not None:
            self.msg = reason
        else:
            self.msg = "Could not assign judges"

    def __str__(self):
        return repr(self.msg)
