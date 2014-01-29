class Error(Exception):
    pass

class ByeAssignmentError(Error):
    def __init__(self):
        pass

class NotEnoughTeamsError(Error):
    def __init__(self):
        pass
    
class RoomAlreadyExistsError(Error):
    def __init__(self):
        pass

class JudgeAlreadyExistsError(Error):
    def __init__(self):
        pass

class SchoolAlreadyExistsError(Error):
    def __init__(self):
        pass

class DebaterAlreadyExistsError(Error):
    def __init__(self):
        pass

class TeamAlreadyExistsError(Error):
    def __init__(self):
        pass
    
class TeamDoesntExistError(Error):
    def __init__(self):
        pass

class RoomDoesntExistError(Error):
    def __init__(self):
        pass

class SchoolDoesntExistError(Error):
    def __init__(self):
        pass
    
class SchoolInUseError(Error):
    def __init__(self):
        pass

class DebaterDoesntExistError(Error):
    def __init__(self):
        pass


class JudgeDoesntExistError(Error):
    def __init__(self):
        pass
    
class NotEnoughJudgesError(Error):
    def __init__(self):
        pass

class NotEnoughRoomsError(Error):
    def __init__(self):
        pass

class DebaterOnMoreThanOneTeamError(Error):
    def __init__(self, debaters):
        self.debaters = debaters
    def __str__(self):
        return repr(self.debaters)

class NeedTwoDifferentDebatersError(Error):
    def __init__(self, debaters):
        pass

class YouAreDumbError(Error):
    def __init__(self):
        self.msg = "Really?  You don't have that many teams at your tournament"
    def __str__(self):
        return repr(self.msg)

class NotEnoughNoviceTeamsError(Error):
    def __init__(self):
        pass

class ToManyScratchesError(Error):
    def __init__(self):
        self.msg = "Judge assignment impossible with current scratches"
    def __str__(self):
        return repr(self.msg)

class JudgeAssignmentError(Error):
    def __init__(self, reason=None):
        if reason is not None:
            self.msg = reason
        else:
            self.msg = "Could not assign judges"

    def __str__(self):
        return repr(self.msg)

class PrevRoundNotEnteredError(Error):
    def __init__(self):
        self.msg = "You have not entered all the results from the previous round."
    def __str__(self):
        return repr(self.msg)

    
