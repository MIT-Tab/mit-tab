#Copyright (C) 2011 by Julia Boortz and Joseph Lynch

#Permission is hereby granted, free of charge, to any person obtaining a copy
#of this software and associated documentation files (the "Software"), to deal
#in the Software without restriction, including without limitation the rights
#to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#copies of the Software, and to permit persons to whom the Software is
#furnished to do so, subject to the following conditions:

#The above copyright notice and this permission notice shall be included in
#all copies or substantial portions of the Software.

#THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
#THE SOFTWARE.


class Error(Exception):
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

class DebaterOnTeams(Error):
    def __init__(self, teams):
        self.teams = teams
    def __str__(self):
        return repr(self.teams)

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

class PrevRoundNotEnteredError(Error):
    def __init(self):
        self.msg = "You have not entered all the results for previous rounds"
    def __str__(self):
        return repr(self.msg)
    
    


    
