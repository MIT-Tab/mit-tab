import pytest
from django.test import TestCase

from mittab.apps.tab.models import (
    Debater,
    NoShow,
    TabSettings,
    Team,
)
from mittab.libs.tab_logic.stats import (
    debater_abnormal_round_speaks,
    debater_abnormal_round_ranks,
    MINIMUM_DEBATER_SPEAKS,
    MAXIMUM_DEBATER_RANKS,
)
from mittab.libs.tab_logic import get_middle_and_non_middle_teams


@pytest.mark.django_db
class TestLenientLate(TestCase):
    """Tests that lenient_late is correctly honored in speaks, ranks, and pairing."""

    fixtures = ["testing_finished_db"]
    pytestmark = pytest.mark.django_db(transaction=True)

    def test_lenient_late_affects_debater_speaks_calculation(self):
        """
        Test that no-shows in lenient rounds use average speaks,
        while non-lenient rounds use minimum speaks (0.0).
        """
        TabSettings.set("cur_round", 6)
        TabSettings.set("lenient_late", 2)
        
        # Get a debater with existing data
        debater = Debater.objects.first()
        team = debater.team()
        
        # Create no-shows for different rounds
        NoShow.objects.create(no_show_team=team, round_number=1)  # lenient
        NoShow.objects.create(no_show_team=team, round_number=3)  # non-lenient
        
        # Lenient round should get average speaks
        speaks_r1 = debater_abnormal_round_speaks(debater, 1)
        self.assertGreater(speaks_r1, MINIMUM_DEBATER_SPEAKS,
                          "Lenient no-show should use average speaks, not minimum")
        
        # Non-lenient round should get minimum speaks
        speaks_r3 = debater_abnormal_round_speaks(debater, 3)
        self.assertEqual(speaks_r3, MINIMUM_DEBATER_SPEAKS,
                        "Non-lenient no-show should use minimum speaks (0.0)")

    def test_lenient_late_affects_debater_ranks_calculation(self):
        """
        Test that no-shows in lenient rounds use average ranks,
        while non-lenient rounds use maximum ranks (3.5).
        """
        TabSettings.set("cur_round", 6)
        TabSettings.set("lenient_late", 2)
        
        # Get a debater with existing data
        debater = Debater.objects.first()
        team = debater.team()
        
        # Create no-shows for different rounds
        NoShow.objects.create(no_show_team=team, round_number=1)  # lenient
        NoShow.objects.create(no_show_team=team, round_number=4)  # non-lenient
        
        # Lenient round should get average ranks
        ranks_r1 = debater_abnormal_round_ranks(debater, 1)
        self.assertLess(ranks_r1, MAXIMUM_DEBATER_RANKS,
                       "Lenient no-show should use average ranks, not maximum")
        
        # Non-lenient round should get maximum ranks
        ranks_r4 = debater_abnormal_round_ranks(debater, 4)
        self.assertEqual(ranks_r4, MAXIMUM_DEBATER_RANKS,
                        "Non-lenient no-show should use maximum ranks (3.5)")

    def test_dynamic_lenient_late_setting_change(self):
        """
        Test that changing the TabSettings.lenient_late value immediately
        affects speaks/ranks calculations without modifying NoShow objects.
        """
        TabSettings.set("cur_round", 6)
        debater = Debater.objects.first()
        team = debater.team()
        
        # Create a no-show in round 2
        no_show = NoShow.objects.create(no_show_team=team, round_number=2)
        
        # With lenient_late=0, should get minimum speaks
        TabSettings.set("lenient_late", 0)
        speaks = debater_abnormal_round_speaks(debater, 2)
        self.assertEqual(speaks, MINIMUM_DEBATER_SPEAKS)
        
        # Change to lenient_late=2, same no-show should now get average speaks
        TabSettings.set("lenient_late", 2)
        no_show.refresh_from_db()  # No DB change, just testing the property
        speaks = debater_abnormal_round_speaks(debater, 2)
        self.assertGreater(speaks, MINIMUM_DEBATER_SPEAKS,
                          "Changing setting should immediately affect speaks")

    def test_pairing_logic_respects_lenient_late(self):
        """
        Test that teams with only lenient no-shows are placed in the middle
        of their bracket during pairing, similar to teams with byes.
        """
        TabSettings.set("cur_round", 3)  # After 2 rounds
        TabSettings.set("lenient_late", 2)
        
        # Get teams and create specific no-show scenarios
        teams = list(Team.objects.all()[:4])
        
        # Team 0: no-show in round 1 (lenient) and round 2 (lenient) - should be in middle
        NoShow.objects.create(no_show_team=teams[0], round_number=1)
        NoShow.objects.create(no_show_team=teams[0], round_number=2)
        
        # Team 1: no-show only in round 1 (lenient) - missing round 2, not in middle
        NoShow.objects.create(no_show_team=teams[1], round_number=1)
        
        # Team 2: no-show in round 1 (lenient) and round 2 (non-lenient after changing setting)
        NoShow.objects.create(no_show_team=teams[2], round_number=1)
        NoShow.objects.create(no_show_team=teams[2], round_number=2)
        
        # Team 3: normal participation, not in middle
        
        middle, non_middle = get_middle_and_non_middle_teams(teams)
        
        # Team 0 should be IN middle (all rounds are lenient no-shows)
        self.assertIn(teams[0], middle, 
                     "Team with only lenient no-shows should be in middle")
        
        # Now change setting so round 2 is no longer lenient
        TabSettings.set("lenient_late", 1)
        middle, non_middle = get_middle_and_non_middle_teams(teams)
        
        # Team 0 should now NOT be in middle (round 2 no longer lenient)
        self.assertNotIn(teams[0], middle,
                        "Team with non-lenient no-show should not be in middle")
        
        # Team 2 should also not be in middle
        self.assertNotIn(teams[2], middle,
                        "Team with non-lenient no-show should not be in middle")
