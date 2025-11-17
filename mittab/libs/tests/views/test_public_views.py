import pytest
from django.core.cache import caches
from django.test import TestCase, Client
from django.urls import reverse
from nplusone.core import profiler

from mittab.apps.tab.models import (Room, TabSettings, Team,
                                    Round, Outround)
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
        TabSettings.set("debaters_public", 1)
        TabSettings.set("tot_rounds", 5)
        TabSettings.set("var_teams_visible", 2)  # Show finals and above
        TabSettings.set("nov_teams_visible", 2)  # Show finals and above

        TabSettings.set("public_rankings_team_public", 1)
        TabSettings.set("public_rankings_team_include_speaks", 0)
        TabSettings.set("public_rankings_team_max_visible", 1000)

        TabSettings.set("public_rankings_varsity_public", 1)
        TabSettings.set("public_rankings_varsity_include_speaks", 1)
        TabSettings.set("public_rankings_varsity_max_visible", 10)

        TabSettings.set("public_rankings_novice_public", 1)
        TabSettings.set("public_rankings_novice_include_speaks", 0)
        TabSettings.set("public_rankings_novice_max_visible", 10)

        TabSettings.set("public_ballots_round_1_visible", 1)
        TabSettings.set("public_ballots_round_1_include_speaks", 0)
        TabSettings.set("public_ballots_round_1_include_ranks", 0)

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
            (reverse("public_speaker_rankings"), ["Speaker Rankings"]),
            (reverse("public_ballots"), ["Public Ballots"]),
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

        # Judges list
        response = client.get(reverse("public_judges"))
        self.assertIn(judge.name, response.content.decode())
        TabSettings.set("judges_public", 0)
        caches["public"].clear()
        response = client.get(reverse("public_judges"))
        self.assertEqual(response.status_code, 302)
        TabSettings.set("judges_public", 1)
        caches["public"].clear()

        # Teams list
        response = client.get(reverse("public_teams"))
        self.assertIn(team.name, response.content.decode())
        TabSettings.set("teams_public", 0)
        caches["public"].clear()
        response = client.get(reverse("public_teams"))
        self.assertEqual(response.status_code, 302)
        TabSettings.set("teams_public", 1)
        caches["public"].clear()

        # Team results page
        response = client.get(reverse("rank_teams_public"))
        self.assertEqual(response.status_code, 200)
        TabSettings.set("public_rankings_team_public", 0)
        caches["public"].clear()
        response = client.get(reverse("rank_teams_public"))
        self.assertEqual(response.status_code, 302)
        TabSettings.set("public_rankings_team_public", 1)
        caches["public"].clear()

        # Speaker results require either varsity or novice to be public
        response = client.get(reverse("public_speaker_rankings"))
        self.assertEqual(response.status_code, 200)
        TabSettings.set("public_rankings_varsity_public", 0)
        TabSettings.set("public_rankings_novice_public", 0)
        caches["public"].clear()
        response = client.get(reverse("public_speaker_rankings"))
        self.assertEqual(response.status_code, 302)
        TabSettings.set("public_rankings_varsity_public", 1)
        TabSettings.set("public_rankings_novice_public", 1)
        caches["public"].clear()

        # Ballots page requires at least one round to be visible
        response = client.get(reverse("public_ballots"))
        self.assertEqual(response.status_code, 200)
        TabSettings.set("public_ballots_round_1_visible", 0)
        caches["public"].clear()
        response = client.get(reverse("public_ballots"))
        self.assertEqual(response.status_code, 302)
        TabSettings.set("public_ballots_round_1_visible", 1)
        caches["public"].clear()

        # Pairings require release toggle
        response = client.get(reverse("pretty_pair"))
        self.assertIn(gov_debater.name, response.content.decode())
        TabSettings.set("pairing_released", 0)
        caches["public"].clear()
        response = client.get(reverse("pretty_pair"))
        self.assertEqual(response.status_code, 200)
        TabSettings.set("pairing_released", 1)
        caches["public"].clear()

        # Outround visibility thresholds
        response = client.get(reverse("outround_pretty_pair", args=[0]))
        self.assertIn(v_out.gov_team.name, response.content.decode())
        response = client.get(reverse("outround_pretty_pair", args=[1]))
        self.assertIn(n_out.gov_team.name, response.content.decode())
        TabSettings.set("var_teams_visible", 16)
        TabSettings.set("nov_teams_visible", 16)
        caches["public"].clear()
        response = client.get(reverse("outround_pretty_pair", args=[0]))
        self.assertEqual(response.status_code, 200)
        response = client.get(reverse("outround_pretty_pair", args=[1]))
        self.assertEqual(response.status_code, 200)
        TabSettings.set("var_teams_visible", 2)
        TabSettings.set("nov_teams_visible", 2)
        caches["public"].clear()


    def test_team_rankings_without_speaks(self):
        client = Client()

        TabSettings.set("public_rankings_team_include_speaks", 0)
        caches["public"].clear()

        response = client.get(reverse("rank_teams_public"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Speaks and ranks are hidden", content)
        self.assertNotIn("<th>Speaks</th>", content)

        TabSettings.set("public_rankings_team_include_speaks", 1)
        caches["public"].clear()
        response = client.get(reverse("rank_teams_public"))
        self.assertIn("<th>Speaks</th>", response.content.decode())

    def test_speaker_rankings_visibility(self):
        client = Client()

        response = client.get(reverse("public_speaker_rankings"))
        content = response.content.decode()
        self.assertIn("Varsity Speakers", content)
        self.assertIn("Novice Speakers", content)

        TabSettings.set("public_rankings_varsity_public", 0)
        caches["public"].clear()
        response = client.get(reverse("public_speaker_rankings"))
        content = response.content.decode()
        self.assertNotIn("Varsity Speakers", content)
        self.assertIn("Novice Speakers", content)

    def test_public_ballots_include_scores(self):
        client = Client()

        TabSettings.set("public_ballots_round_1_visible", 1)
        TabSettings.set("public_ballots_round_1_include_speaks", 0)
        TabSettings.set("public_ballots_round_1_include_ranks", 0)
        caches["public"].clear()
        cache_logic.invalidate_cache("public_ballots_round_1")

        response = client.get(reverse("public_ballots"))
        content = response.content.decode()
        self.assertNotIn("badge badge-light", content)

        TabSettings.set("public_ballots_round_1_include_speaks", 1)
        TabSettings.set("public_ballots_round_1_include_ranks", 1)
        caches["public"].clear()
        cache_logic.invalidate_cache("public_ballots_round_1")
        response = client.get(reverse("public_ballots"))
        content = response.content.decode()
        self.assertIn("badge badge-light", content)

    def test_n_plus_one(self):
        client = Client()

        views_to_test = [
            (("public_judges",), None),
            (("public_teams",), None),
            (("rank_teams_public",), None),
             (("public_speaker_rankings",), None),
             (("public_ballots",), None),
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
