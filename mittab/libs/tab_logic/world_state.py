"""
The normal ORM way to query things leads to tons of DB queries,
sometimes thousands. Rather than do that, this class just loads
all of the relevant data into memory and performs in-memory calculations
using the state at the time it was loaded
"""

class TabLogicWorldState:
    def __initialize__(self):
        self.teams = Team.objects.all()
        self.tab_settings = TabSettings.objects.all()
