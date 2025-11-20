import pytest
from django.core.cache import caches
from django.test import TestCase, Client
from django.urls import reverse
from nplusone.core import profiler

from mittab.apps.tab.models import (
    Room,
    TabSettings,
    Team,
    Round,
    Outround,
    Judge,
    School,
    JudgeExpectedCheckIn,
)
from mittab.apps.tab.public_rankings import PublicRankingMode
from mittab.libs.cacheing import cache_logic


@pytest.mark.django_db(transaction=True)
class TestPublicViews(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        caches["public"].clear()
        cache_logic.clear_cache()

        # Ensure ballots are eligible for public display regardless of fixture defaults
        Team.objects.update(ranking_public=True)

        Outround(
            gov_team=Team.objects.first(),
            opp_team=Team.objects.last(),
            num_teams=2,
            type_of_round=Outround.NOVICE,
            room=Room.objects.first(),
        ).save()
        Outround(
            gov_team=Team.objects.first(),
            opp_team=Team.objects.last(),
            num_teams=2,
            type_of_round=Outround.VARSITY,
            room=Room.objects.last(),
        ).save()

        # Pairing for the next round (3) has started, so round 2 ballots may show
        TabSettings.set("cur_round", 3)

        # Mark the most recent round (cur_round - 1) as missing a ballot
        self.test_round = Round.objects.filter(
            round_number=TabSettings.get("cur_round") - 1
        ).first()
        self.original_victor = self.test_round.victor
        self.test_round.victor = Round.NONE
        self.test_round.save()
        TabSettings.set("pairing_released", 1)
        TabSettings.set("judges_public", 1)
        TabSettings.set("teams_public", 1)
        TabSettings.set("public_ranking_mode", PublicRankingMode.TEAM)
        TabSettings.set("public_ballot_show_speaks", 0)
        TabSettings.set("debaters_public", 1)
        TabSettings.set("var_teams_visible", 2)  # Show finals and above
        TabSettings.set("nov_teams_visible", 2)  # Show finals and above

    def tearDown(self):
        # Restore the original victor value to avoid polluting other tests
        self.test_round.victor = self.original_victor
        self.test_round.save()
        caches["public"].clear()
        cache_logic.clear_cache()
        super().tearDown()

    def get_test_objects(self):
        judge = self.test_round.chair
        team = Team.objects.first()
        round_obj = Round.objects.filter(round_number=1, gov_team__isnull=False).first()
        v_out = Outround.objects.filter(type_of_round=0).first()
        n_out = Outround.objects.filter(type_of_round=1).first()
        gov_debater = round_obj.gov_team.debaters.first()

        # Ensure judge has a ballot code for e-ballot tests
        judge.ballot_code = "TEST123"
        judge.save()

        return judge, team, round_obj, v_out, n_out, gov_debater

    def test_render(self):
        client = Client()

        (judge, team, round_obj, v_out,
         n_out, gov_debater) = self.get_test_objects()

        view_content_tests = [
            (reverse("public_judges"), [judge.name]),
            (reverse("public_teams"), [team.name]),
            (reverse("rank_teams_public"), [team.name]),
            (reverse("pretty_pair"), [round_obj.gov_team.name, gov_debater.name]),
            (reverse("missing_ballots"), [self.test_round.chair.name]),
            (reverse("e_ballot_search"), ["Submit E-Ballot"]),
            (reverse("outround_pretty_pair", args=[0]), [v_out.gov_team.name]),
            (reverse("outround_pretty_pair", args=[1]), [n_out.gov_team.name]),
            (reverse("public_home"), ["Released Pairings"]),
        ]

        for url, expected_content in view_content_tests:
            response = client.get(url)
            self.assertEqual(response.status_code, 200,
                f"Failed to render {url}, got status {response.status_code}")

            for content in expected_content:
                self.assertIn(content, response.content.decode(),
                    f"Expected content '{content}' not found in {url}")

        if judge and judge.ballot_code:
            chair_round = Round.objects.filter(round_number=1).first()
            if chair_round:
                chair_round.chair = judge
                chair_round.save()

                url = reverse("enter_e_ballot", args=[judge.ballot_code])
                response = client.get(url)
                self.assertIn(response.status_code, [200, 302],
                    "Failed to handle e-ballot entry for valid ballot code")

    def test_permissions(self):
        client = Client()

        (judge, team, _, v_out,
         n_out, gov_debater) = self.get_test_objects()

        urls = [
            reverse("public_judges"),
            reverse("public_teams"),
            reverse("rank_teams_public"),
            reverse("pretty_pair"),
            reverse("missing_ballots"),
            reverse("outround_pretty_pair", args=[0]),
            reverse("outround_pretty_pair", args=[1]),
        ]

        # Setting name, allowed value, denied value
        settings = [
            ("judges_public", 1, 0),
            ("teams_public", 1, 0),
            ("public_ranking_mode", PublicRankingMode.TEAM, PublicRankingMode.NONE),
            ("pairing_released", 1, 0),
            ("pairing_released", 1, 0),
            ("var_teams_visible", 2, 16),
            ("nov_teams_visible", 2, 16),
        ]

        # content, status when allowed, status when denied
        values = [
            (judge.name, 200, 302),
            (team.name, 200, 302),
            (team.name, 200, 302),
            (gov_debater.name, 200, 200),
            (self.test_round.gov_team.display_backend, 200, 200),
            (v_out.gov_team.name, 200, 200),
            (n_out.gov_team.name, 200, 200),
        ]

        for url, setting, value in zip(urls, settings, values):
            (setting_name, allowed_value, denied_value) = setting
            (expected_content, status_allowed, status_denied) = value
            # Test when permission is granted / content visible
            TabSettings.set(setting_name, allowed_value)
            response = client.get(url)
            self.assertEqual(response.status_code, status_allowed,
                f"Expected {status_allowed} for {url} "
                f"when {setting_name}={allowed_value}")
            self.assertIn(expected_content, response.content.decode(),
                f"Expected '{expected_content}' to be "
                f"visible when {setting_name}={allowed_value}")

            # Test when permission is denied / content hidden
            TabSettings.set(setting_name, denied_value)
            caches["public"].clear()
            response = client.get(url)
            self.assertEqual(response.status_code, status_denied,
                f"Expected {status_denied} for {url} "
                f"when {setting_name}={denied_value}")
            self.assertNotIn(expected_content, response.content.decode(),
                f"Expected '{expected_content}' to be "
                f"hidden when {setting_name}={denied_value}")


    def test_public_judges_use_expectations(self):
        client = Client()
        school = School.objects.first()
        judge = Judge.objects.create(name="Expectation Judge", rank=2.0)
        judge.schools.add(school)

        JudgeExpectedCheckIn.objects.create(judge=judge, round_number=1)

        response = client.get(reverse("public_judges"))
        self.assertEqual(response.status_code, 200)

        content = response.content.decode()
        row_start = content.find(judge.name)
        self.assertNotEqual(row_start, -1, "Judge row not rendered")
        row_end = content.find("</tr>", row_start)
        self.assertNotEqual(row_end, -1, "Judge row not properly closed")
        row_html = content[row_start:row_end]
        self.assertIn("&#10004;", row_html,
                      "Expected attendance indicator not shown for judge")

    def test_public_ballot_modes(self):
        client = Client()

        TabSettings.set("public_ranking_mode", PublicRankingMode.LAST_BALLOTS)
        caches["public"].clear()
        response = client.get(reverse("rank_teams_public"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Last Round Ballots", content)
        self.assertIn("Winner:", content)
        self.assertIn("Speaks and ranks are hidden", content)

        TabSettings.set("public_ranking_mode", PublicRankingMode.ALL_BALLOTS)
        TabSettings.set("public_ballot_show_speaks", 1)
        caches["public"].clear()
        response = client.get(reverse("rank_teams_public"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("All Ballots", content)
        self.assertNotIn("Speaks and ranks are hidden", content)

    def test_team_rankings_without_speaks(self):
        client = Client()

        TabSettings.set("public_ranking_mode", PublicRankingMode.TEAM)
        TabSettings.set("public_ballot_show_speaks", 0)
        caches["public"].clear()

        response = client.get(reverse("rank_teams_public"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Speaks and ranks are hidden", content)
        self.assertNotIn("<th>Speaks</th>", content)

    def test_public_ballots_hidden_until_next_round(self):
        client = Client()

        TabSettings.set("public_ranking_mode", PublicRankingMode.LAST_BALLOTS)
        TabSettings.set("latest_ballots_released", 0)

        # With cur_round=2 no subsequent round has been paired, so nothing shows
        TabSettings.set("cur_round", 2)
        caches["public"].clear()
        cache_logic.invalidate_cache("public_ballots_last")
        response = client.get(reverse("rank_teams_public"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("No public ballots are available yet", response.content.decode())

        # Once the next round is paired (cur_round=3), ballots become visible
        TabSettings.set("cur_round", 3)
        TabSettings.set("latest_ballots_released", 1)
        caches["public"].clear()
        cache_logic.invalidate_cache("public_ballots_last")
        response = client.get(reverse("rank_teams_public"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Winner:", response.content.decode())

    def test_manual_release_allows_current_round_ballots(self):
        client = Client()

        TabSettings.set("public_ranking_mode", PublicRankingMode.LAST_BALLOTS)
        TabSettings.set("cur_round", 2)
        TabSettings.set("latest_ballots_released", 0)
        caches["public"].clear()
        cache_logic.invalidate_cache("public_ballots_last")

        response = client.get(reverse("rank_teams_public"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("No public ballots are available yet", response.content.decode())

        TabSettings.set("latest_ballots_released", 1)
        caches["public"].clear()
        cache_logic.invalidate_cache("public_ballots_last")
        response = client.get(reverse("rank_teams_public"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Winner:", response.content.decode())

        TabSettings.set("latest_ballots_released", 0)


    def test_n_plus_one(self):
        client = Client()

        views_to_test = [
            (("public_judges",), None),
            (("public_teams",), None),
            (("rank_teams_public",), None),
            (("pretty_pair",), None),
            (("missing_ballots",), None),
            (("outround_pretty_pair",), [0]),
            (("outround_pretty_pair",), [1]),
            (("public_home",), None),
        ]

        for view_name, url_args in views_to_test:
            with profiler.Profiler():
                if url_args:
                    response = client.get(reverse(*view_name, args=url_args))
                else:
                    response = client.get(reverse(*view_name))
                self.assertEqual(response.status_code, 200)
