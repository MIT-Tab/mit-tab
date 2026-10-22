"""Publication, bulk rendering, and cache isolation for tab cards."""
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from nplusone.core import profiler

from mittab.apps.tab.models import Outround, TabSettings, Team
from mittab.apps.tab.tab_cards import get_tab_cards
from mittab.apps.tab.views.views import get_settings_from_yaml
from mittab.libs.cacheing import cache_logic
from mittab.libs.cacheing.public_cache import invalidate_all_public_caches


class TestTabCards(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        caches["public"].clear()
        cache_logic.clear_cache()
        Outround.objects.all().delete()
        self.team = Team.objects.first()
        self.outround = Outround.objects.create(
            gov_team=self.team,
            opp_team=Team.objects.last(),
            num_teams=8,
            type_of_round=Outround.VARSITY,
        )
        TabSettings.set("var_teams_visible", 256)
        TabSettings.set("nov_teams_visible", 256)
        TabSettings.objects.filter(key="publish_tab_cards_with_first_outround").delete()
        self.public_url = reverse("public_tab_cards")

    def tearDown(self):
        caches["public"].clear()
        cache_logic.clear_cache()
        super().tearDown()

    def release(self, division=Outround.VARSITY):
        key = ("var_teams_visible" if division == Outround.VARSITY
               else "nov_teams_visible")
        TabSettings.set(key, 8)

    def login_staff(self):
        user = get_user_model().objects.create_superuser(
            username="tab-card-staff", email="cards@example.com", password="testpass"
        )
        self.client.force_login(user)

    def test_setting_defaults_to_enabled(self):
        settings, _, _ = get_settings_from_yaml()
        setting = next(item for item in settings
                       if item["name"] == "publish_tab_cards_with_first_outround")
        self.assertTrue(setting["value"])
        self.assertFalse(setting["persisted"])

    def test_cards_publish_only_after_a_real_outround_is_released(self):
        self.assertRedirects(
            self.client.get(self.public_url), reverse("public_access_error")
        )
        self.release()
        response = self.client.get(self.public_url)
        self.assertContains(response, "Tournament Totals:")
        self.assertContains(response, self.team.debaters.first().name)
        self.assertNotContains(response, "Staff view")
        self.assertNotContains(response, "Loading...")
        self.assertNotContains(response, "data-team-id")
        expected_count = Team.objects.exclude(debaters=None).distinct().count()
        self.assertContains(response, 'class="tab-card table-bordered table"',
                            count=expected_count)
        Outround.objects.all().delete()
        self.assertRedirects(
            self.client.get(self.public_url), reverse("public_access_error")
        )

    def test_novice_outround_also_releases_cards(self):
        self.outround.type_of_round = Outround.NOVICE
        self.outround.save()
        self.release()
        self.assertRedirects(
            self.client.get(self.public_url), reverse("public_access_error")
        )
        self.release(Outround.NOVICE)
        self.assertContains(self.client.get(self.public_url), "Tournament Totals:")

    def test_cached_cards_are_blocked_when_disabled_or_pairings_hidden(self):
        self.release()
        self.assertEqual(self.client.get(self.public_url).status_code, 200)
        TabSettings.set("publish_tab_cards_with_first_outround", 0)
        response = self.client.get(self.public_url)
        self.assertRedirects(response, reverse("public_access_error"))
        self.assertIn("no-store", response["Cache-Control"])
        TabSettings.set("publish_tab_cards_with_first_outround", 1)
        self.assertEqual(self.client.get(self.public_url).status_code, 200)
        TabSettings.set("var_teams_visible", 256)
        self.assertRedirects(
            self.client.get(self.public_url), reverse("public_access_error")
        )

    def test_public_cards_are_cached_and_invalidated(self):
        self.release()
        with patch("mittab.apps.tab.views.tab_card_views.get_tab_cards",
                   wraps=get_tab_cards) as builder:
            first = self.client.get(self.public_url)
            second = self.client.get(self.public_url)
            self.assertEqual(first.content, second.content)
            self.assertEqual(builder.call_count, 1)
            self.assertIn("public", first["Cache-Control"])
            self.assertIn("s-maxage=10", first["Cache-Control"])
            self.assertNotIn("sessionid", first.cookies)
            invalidate_all_public_caches()
            self.client.get(self.public_url)
            self.assertEqual(builder.call_count, 2)

    def test_staff_routes_are_uncached_and_public_route_redirects_to_staff(self):
        self.release()
        self.client.get(self.public_url)
        self.login_staff()
        response = self.client.get(self.public_url)
        self.assertRedirects(response, reverse("all_tab_cards"))
        self.assertIn("no-store", response["Cache-Control"])
        urls = [
            reverse("all_tab_cards"),
            reverse("pretty_tab_card", args=[self.team.pk]),
            reverse("tab_card", args=[self.team.pk]),
        ]
        with patch("mittab.apps.tab.views.team_views.get_tab_cards",
                   wraps=get_tab_cards) as builder:
            for url in urls:
                for _ in range(2):
                    response = self.client.get(url)
                    self.assertContains(response, "Tournament Totals:")
                    self.assertIn("no-store", response["Cache-Control"])
                    self.assertNotIn("public", response["Cache-Control"])
                    if url == reverse("all_tab_cards"):
                        self.assertTemplateNotUsed(response, "base/__wide.html")
                        self.assertNotContains(response, "<nav")
                        self.assertNotContains(response, "<script")
                        self.assertNotContains(response, "webpack_bundles/")
                        self.assertContains(response, "<h3>Team:")
                        self.assertContains(response, "css/tab-cards-print.css")
            self.assertEqual(builder.call_count, 6)
        public_response = Client().get(self.public_url)
        self.assertNotContains(public_response, "Staff view")
        self.assertNotContains(public_response, "Logout")
        self.assertTemplateUsed(public_response, "public/tab_cards.html")
        self.assertContains(public_response, "<nav")

    def test_private_routes_require_login_before_publication(self):
        for url in [reverse("all_tab_cards"),
                    reverse("pretty_tab_card", args=[self.team.pk]),
                    reverse("tab_card", args=[self.team.pk])]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("tab_login"), response.url)

    def test_homepage_and_navigation_track_publication(self):
        home_url = reverse("public_home")
        self.assertNotContains(self.client.get(home_url), self.public_url)
        self.release()
        response = self.client.get(home_url)
        self.assertContains(
            response, f'class="tile shadow-sm" href="{self.public_url}"'
        )
        self.assertContains(response, f'href="{self.public_url}"', count=2)
        links = [link["slug"] for link in response.context["quick_links"]]
        self.assertEqual(links.count("public_tab_cards"), 1)
        for slug in ("released_pairings", "missing_ballots", "public_team_results",
                     "public_speaker_results", "public_ballots"):
            self.assertNotIn(slug, links)
        TabSettings.set("publish_tab_cards_with_first_outround", 0)
        self.assertNotContains(self.client.get(home_url), self.public_url)

    def test_bulk_cards_have_bounded_queries(self):
        with profiler.Profiler():
            with CaptureQueriesContext(connection) as bulk_queries:
                cards = get_tab_cards()
        self.assertGreater(len(cards), 10)
        self.assertLessEqual(len(bulk_queries), 25)

    def test_teams_without_debaters_do_not_break_other_cards(self):
        Team.objects.create(name="No debaters yet", school=self.team.school, seed=0)
        self.release()
        self.assertContains(self.client.get(self.public_url), "Tournament Totals:")

    def test_refreshed_public_cards_keep_cache_headers(self):
        self.release()
        self.client.get(self.public_url)
        with patch("mittab.libs.cacheing.public_cache.time.time",
                   return_value=time.time() + 61):
            response = self.client.get(self.public_url)
        self.assertIn("public", response["Cache-Control"])
        self.assertIn("s-maxage=10", response["Cache-Control"])

    def test_logged_in_visitor_does_not_share_anonymous_cache(self):
        self.release()
        self.client.get(self.public_url)
        user = get_user_model().objects.create_user(
            username="limited-card-staff", password="testpass", is_staff=True,
        )
        self.client.force_login(user)
        with patch("mittab.apps.tab.views.tab_card_views.get_tab_cards",
                   wraps=get_tab_cards) as builder:
            for _ in range(2):
                response = self.client.get(self.public_url)
                self.assertContains(response, "Tournament Totals:")
                self.assertIn("no-store", response["Cache-Control"])
            self.assertEqual(builder.call_count, 2)
        self.assertNotContains(Client().get(self.public_url), "Logout")
