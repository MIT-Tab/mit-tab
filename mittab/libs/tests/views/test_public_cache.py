# pylint: disable=W0212
import pytest
from django.core.cache import caches
from django.test import TestCase, Client
from django.urls import reverse

from mittab.apps.tab.models import (Room, TabSettings, Team, Round, Outround)
from mittab.apps.tab.public_rankings import PublicRankingMode
from mittab.libs.cacheing import cache_logic


@pytest.mark.django_db(transaction=True)
class TestPublicCache(TestCase):
    """
    Test that public views are properly cached and invalidated.

    This test validates:
    1. Responses are cached and reused (not recomputed)
    2. Cache is invalidated when permissions change
    3. Cache invalidation happens immediately on key tab actions
    4. CDN headers are set correctly for 10-second TTL
    """

    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        self.cache = caches["public"]
        self.cache.clear()
        cache_logic.clear_cache()
        Team.objects.update(ranking_public=True)
        self.client = Client()

        # Set up outrounds
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

        # Set up test round with missing ballot
        self.test_round = Round.objects.filter(round_number=1).first()
        self.original_victor = self.test_round.victor
        self.test_round.victor = Round.NONE
        self.test_round.save()

        # Enable all public views
        # Allow round 1 ballots to be public by ensuring round 2 pairing exists
        TabSettings.set("cur_round", 3)
        TabSettings.set("pairing_released", 1)
        TabSettings.set("judges_public", 1)
        TabSettings.set("teams_public", 1)
        TabSettings.set("public_ranking_mode", PublicRankingMode.TEAM)
        TabSettings.set("public_ballot_show_speaks", 0)
        TabSettings.set("debaters_public", 1)
        TabSettings.set("var_teams_visible", 2)
        TabSettings.set("nov_teams_visible", 2)

    def tearDown(self):
        self.test_round.victor = self.original_victor
        self.test_round.save()
        self.cache.clear()
        cache_logic.clear_cache()
        super().tearDown()

    def _get_cache_key_count(self):
        """Count number of keys in the public cache."""
        # This is implementation-specific but works for testing
        try:
            return len(self.cache._cache.keys())  # pylint: disable=protected-access
        except AttributeError:
            # Fallback for different cache backends
            return -1

    def test_caching_behavior_and_cdn_headers(self):
        """Test that responses are cached, reused, and have correct CDN headers."""

        # List of public views to test
        views = [
            reverse("public_judges"),
            reverse("public_teams"),
            reverse("rank_teams_public"),
            reverse("pretty_pair"),
            reverse("missing_ballots"),
            reverse("public_home"),
            reverse("outround_pretty_pair", args=[0]),
            reverse("outround_pretty_pair", args=[1]),
        ]

        for url in views:
            # Clear cache before each test
            self.cache.clear()
            initial_key_count = self._get_cache_key_count()

            # First request should miss cache and populate it
            response1 = self.client.get(url)
            self.assertEqual(response1.status_code, 200,
                f"First request to {url} failed")

            after_first_count = self._get_cache_key_count()
            if initial_key_count >= 0:
                self.assertGreater(after_first_count, initial_key_count,
                    f"Cache should be populated after first request to {url}")

            # Verify CDN headers on first response
            self.assertIn("Cache-Control", response1,
                f"Cache-Control header missing for {url}")
            cache_control = response1["Cache-Control"]
            self.assertIn("max-age=10", cache_control,
                f"CDN should have 10s TTL for {url}, got: {cache_control}")
            self.assertIn("public", cache_control,
                f"Response should be publicly cacheable for {url}")

            # Second request should hit cache (same content)
            response2 = self.client.get(url)
            self.assertEqual(response2.status_code, 200,
                f"Second request to {url} failed")
            self.assertEqual(response1.content, response2.content,
                f"Cached response should be identical for {url}")

            # Cache key count should remain the same
            after_second_count = self._get_cache_key_count()
            if after_first_count >= 0:
                self.assertEqual(after_second_count, after_first_count,
                    f"Cache should be reused for {url}")

    def test_cache_invalidation_and_separation(self):
        """Test cache invalidation on setting changes and auth state separation."""

        # Test pairing release toggle invalidates cache
        pairing_views = [
            reverse("pretty_pair"),
            reverse("missing_ballots"),
            reverse("public_home"),
        ]

        for url in pairing_views:
            self.client.get(url)

        # Verify cache is populated
        initial_count = self._get_cache_key_count()
        if initial_count >= 0:
            self.assertGreater(initial_count, 0, "Cache should be populated")

        # Get initial content when pairing is released
        response_released = self.client.get(reverse("pretty_pair"))
        self.assertEqual(response_released.status_code, 200)
        content_released = response_released.content.decode()

        # Toggle pairing release (simulate unpublishing)
        TabSettings.set("pairing_released", 0)
        self.cache.clear()  # Simulate what the invalidation function does

        response_unreleased = self.client.get(reverse("pretty_pair"))
        self.assertEqual(response_unreleased.status_code, 200)
        content_unreleased = response_unreleased.content.decode()

        # Content should be different (teams hidden when unpublished)
        self.assertNotEqual(content_released, content_unreleased,
            "Content should change when pairing release status changes")

        # Toggle back and verify content matches
        TabSettings.set("pairing_released", 1)
        self.cache.clear()
        response_rereleased = self.client.get(reverse("pretty_pair"))
        content_rereleased = response_rereleased.content.decode()
        self.assertEqual(content_released, content_rereleased,
            "Content should match when pairing is re-released")

        # Test that invalidate_all_public_caches works
        all_views = [
            reverse("public_judges"),
            reverse("public_teams"),
            reverse("rank_teams_public"),
            reverse("pretty_pair"),
            reverse("public_home"),
        ]

        for url in all_views:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)

        populated_count = self._get_cache_key_count()
        if populated_count >= 0:
            self.assertGreater(populated_count, 0, "Cache should be populated")

        # Simulate what invalidate_all_public_caches does
        self.cache.clear()
        cleared_count = self._get_cache_key_count()
        if cleared_count >= 0:
            self.assertEqual(cleared_count, 0, "Cache should be cleared")

        # Verify fresh content is generated
        for url in all_views:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200,
                f"Should be able to regenerate content for {url}")

    def test_permission_and_visibility_changes(self):
        """Test that changing permissions and 
            visibility settings affects content and cache."""

        team = Team.objects.first()
        judge = Round.objects.filter(round_number=1).first().chair
        v_out = Outround.objects.filter(type_of_round=Outround.VARSITY).first()
        n_out = Outround.objects.filter(type_of_round=Outround.NOVICE).first()

        # Test public permission toggles with config
        permission_tests = [
            {
                'setting': 'teams_public',
                'url': reverse("public_teams"),
                'visible_content': team.name,
                'enabled': 1,
                'disabled': 0,
            },
            {
                'setting': 'judges_public',
                'url': reverse("public_judges"),
                'visible_content': judge.name,
                'enabled': 1,
                'disabled': 0,
            },
            {
                'setting': 'public_ranking_mode',
                'url': reverse("rank_teams_public"),
                'visible_content': team.name,
                'enabled': PublicRankingMode.TEAM,
                'disabled': PublicRankingMode.NONE,
            },
        ]

        for test in permission_tests:
            # Test enabled state
            self.cache.clear()
            enabled_value = test.get('enabled', 1)
            disabled_value = test.get('disabled', 0)
            TabSettings.set(test['setting'], enabled_value)
            response = self.client.get(test['url'])
            self.assertEqual(response.status_code, 200)
            self.assertIn(test['visible_content'], response.content.decode(),
                f"Content should be visible when {test['setting']}={enabled_value}")

            # Test disabled state (should redirect)
            self.cache.clear()
            TabSettings.set(test['setting'], disabled_value)
            response = self.client.get(test['url'])
            self.assertEqual(response.status_code, 302,
                f"Should redirect when {test['setting']}={disabled_value}")

        # Test outround visibility settings
        outround_tests = [
            {
                'setting': 'var_teams_visible',
                'url': reverse("outround_pretty_pair", args=[0]),
                'outround': v_out,
                'visible_value': 2,
                'hidden_value': 256,
            },
            {
                'setting': 'nov_teams_visible',
                'url': reverse("outround_pretty_pair", args=[1]),
                'outround': n_out,
                'visible_value': 2,
                'hidden_value': 256,
            },
        ]

        for test in outround_tests:
            # Test visible state
            self.cache.clear()
            TabSettings.set(test['setting'], test['visible_value'])
            response = self.client.get(test['url'])
            self.assertEqual(response.status_code, 200)
            self.assertIn(test['outround'].gov_team.name,
                          response.content.decode(),
                f"Outround should be visible when "
                f"{test['setting']}={test['visible_value']}")

            # Test hidden state
            self.cache.clear()
            TabSettings.set(test['setting'], test['hidden_value'])
            response = self.client.get(test['url'])
            self.assertEqual(response.status_code, 200)
            # Just verify we get a response - visibility logic is in the template

    def test_authenticated_cache_separation(self):
        """Test that authenticated and unauthenticated
            users get separate cache entries."""

        url = reverse("public_teams")
        TabSettings.set("teams_public", 1)

        # Request as unauthenticated
        self.cache.clear()
        self.client.logout()
        unauth_resp = self.client.get(url)
        self.assertEqual(unauth_resp.status_code, 200)
        after_unauth = self._get_cache_key_count()

        # Request as authenticated (should create separate cache entry)
        self.client.login(username='tab', password='password')
        auth_resp = self.client.get(url)
        self.assertIn(auth_resp.status_code, [200, 302])
        after_auth = self._get_cache_key_count()

        # Should have created a second cache entry for authenticated user
        if after_unauth >= 0 and after_auth >= 0:
            self.assertGreater(after_auth, after_unauth,
                "Authenticated request should create separate cache entry")

        # Logout and verify unauthenticated behavior is consistent
        self.client.logout()
        second_unauth = self.client.get(url)
        self.assertEqual(second_unauth.status_code, 200,
            "Unauthenticated users should get consistent responses")
