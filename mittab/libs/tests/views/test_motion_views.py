import pytest
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import Client, TestCase
from django.urls import reverse
from nplusone.core import profiler

from mittab.apps.tab.models import Motion, TabSettings
from mittab.libs.cacheing import cache_logic


@pytest.mark.django_db(transaction=True)
class TestMotionViews(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            username="motion_admin",
            password="testpass123",
            email="motion_admin@test.com",
        )
        self.client.login(username="motion_admin", password="testpass123")
        caches["public"].clear()
        cache_logic.clear_cache()
        TabSettings.set("motions_enabled", 1)
        TabSettings.set("tot_rounds", 5)

    def tearDown(self):
        caches["public"].clear()
        cache_logic.clear_cache()
        super().tearDown()

    def test_manage_motions_disabled_redirects_to_settings(self):
        TabSettings.set("motions_enabled", 0)

        response = self.client.get(reverse("manage_motions"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Motions feature is disabled", response.content.decode())
        self.assertIn(reverse("settings_form"), response.request["PATH_INFO"])

    def test_add_motion_and_edit_page_selects_current_inround(self):
        response = self.client.post(
            reverse("add_motion"),
            {
                "round_selection": "inround_2",
                "info_slide": "Context",
                "motion_text": "This House would test motion storage.",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        motion = Motion.objects.get()
        self.assertEqual(motion.round_number, 2)
        self.assertFalse(motion.is_outround)

        edit_response = self.client.get(reverse("edit_motion", args=[motion.pk]))
        self.assertEqual(edit_response.status_code, 200)
        content = edit_response.content.decode()
        self.assertRegex(
            content,
            r'value="inround_2"\s+selected',
            "Current inround option should be pre-selected on edit page.",
        )

    def test_add_motion_unexpected_parse_error_shows_generic_message(self):
        response = self.client.post(
            reverse("add_motion"),
            {
                "round_selection": "outround_foo",
                "motion_text": "This House would never parse this.",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Unexpected error adding motion. Please try again.", content)
        self.assertNotIn("invalid literal for int()", content)
        self.assertEqual(Motion.objects.count(), 0)

    def test_toggle_and_bulk_publish_actions(self):
        motion_one = Motion.objects.create(round_number=1, motion_text="Motion 1")

        toggle_response = self.client.post(
            reverse("toggle_motion_published", args=[motion_one.pk]),
            follow=True,
        )
        self.assertEqual(toggle_response.status_code, 200)
        motion_one.refresh_from_db()
        self.assertTrue(motion_one.is_published)

        publish_all_response = self.client.post(reverse("publish_all_motions"),
                                                follow=True)
        self.assertEqual(publish_all_response.status_code, 200)
        self.assertEqual(Motion.objects.filter(is_published=True).count(), 2)

        unpublish_all_response = self.client.post(
            reverse("unpublish_all_motions"),
            follow=True,
        )
        self.assertEqual(unpublish_all_response.status_code, 200)
        self.assertEqual(Motion.objects.filter(is_published=False).count(), 2)
        self.assertEqual(Motion.objects.count(), 2)

    def test_public_motions_respects_feature_flag_and_publication_status(self):
        Motion.objects.create(
            round_number=1,
            motion_text="Published prelim motion",
            is_published=True,
        )
        Motion.objects.create(
            round_number=2,
            motion_text="Draft prelim motion",
            is_published=False,
        )
        Motion.objects.create(
            outround_type=Motion.VARSITY,
            num_teams=8,
            motion_text="Varsity quarters motion",
            is_published=True,
        )

        public_client = Client()
        response = public_client.get(reverse("public_motions"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Published prelim motion", content)
        self.assertIn("Varsity quarters motion", content)
        self.assertNotIn("Draft prelim motion", content)
        self.assertIn("Preliminary Rounds", content)
        self.assertIn("Varsity Elimination Rounds", content)

        TabSettings.set("motions_enabled", 0)
        caches["public"].clear()
        disabled_response = public_client.get(reverse("public_motions"))
        self.assertEqual(disabled_response.status_code, 302)
        self.assertEqual(disabled_response.url, reverse("public_access_error"))

    def test_n_plus_one(self):
        views_to_test = [
            reverse("manage_motions"),
            reverse("public_motions"),
        ]

        for url in views_to_test:
            with profiler.Profiler():
                response = self.client.get(url)
                self.assertEqual(
                    response.status_code,
                    200,
                    f"Failed to render {url}, got status {response.status_code}",
                )
