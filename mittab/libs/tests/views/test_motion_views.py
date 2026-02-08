from unittest.mock import patch

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

    def test_add_motion_non_post_redirects(self):
        response = self.client.get(reverse("add_motion"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("manage_motions"))

    def test_add_motion_requires_fields_and_rejects_invalid_round(self):
        missing_text_response = self.client.post(
            reverse("add_motion"),
            {"round_selection": "inround_1", "motion_text": "   "},
            follow=True,
        )
        self.assertEqual(missing_text_response.status_code, 200)
        self.assertIn(
            "Motion text is required.",
            missing_text_response.content.decode(),
        )

        missing_round_response = self.client.post(
            reverse("add_motion"),
            {"motion_text": "Valid motion"},
            follow=True,
        )
        self.assertEqual(missing_round_response.status_code, 200)
        self.assertIn(
            "Round selection is required.",
            missing_round_response.content.decode(),
        )

        invalid_round_response = self.client.post(
            reverse("add_motion"),
            {"round_selection": "bad_round", "motion_text": "Valid motion"},
            follow=True,
        )
        self.assertEqual(invalid_round_response.status_code, 200)
        self.assertIn(
            "Invalid round selection.",
            invalid_round_response.content.decode(),
        )

    def test_add_motion_creates_outround_and_invalidates_cache(self):
        with patch(
            "mittab.apps.tab.views.motion_views.invalidate_public_motions_cache"
        ) as invalidate_cache:
            response = self.client.post(
                reverse("add_motion"),
                {
                    "round_selection": "outround_1_8",
                    "info_slide": "Novice context",
                    "motion_text": "This House would test novice quarters.",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        motion = Motion.objects.get()
        self.assertEqual(motion.outround_type, Motion.NOVICE)
        self.assertEqual(motion.num_teams, 8)
        self.assertEqual(motion.round_number, None)
        invalidate_cache.assert_called_once()

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

    def test_edit_motion_get_and_non_post_actions_redirect(self):
        motion = Motion.objects.create(round_number=1, motion_text="Motion")

        edit_get_response = self.client.get(reverse("edit_motion", args=[motion.pk]))
        self.assertEqual(edit_get_response.status_code, 200)
        self.assertIn("Edit Motion", edit_get_response.content.decode())

        delete_get_response = self.client.get(
            reverse("delete_motion", args=[motion.pk])
        )
        self.assertEqual(delete_get_response.status_code, 302)
        self.assertEqual(delete_get_response.url, reverse("manage_motions"))

        toggle_get_response = self.client.get(
            reverse("toggle_motion_published", args=[motion.pk])
        )
        self.assertEqual(toggle_get_response.status_code, 302)
        self.assertEqual(toggle_get_response.url, reverse("manage_motions"))

        publish_all_get = self.client.get(reverse("publish_all_motions"))
        self.assertEqual(publish_all_get.status_code, 302)
        self.assertEqual(publish_all_get.url, reverse("manage_motions"))

        unpublish_all_get = self.client.get(reverse("unpublish_all_motions"))
        self.assertEqual(unpublish_all_get.status_code, 302)
        self.assertEqual(unpublish_all_get.url, reverse("manage_motions"))

    def test_edit_motion_validation_and_exception_paths(self):
        motion = Motion.objects.create(round_number=1, motion_text="Original motion")

        missing_text_response = self.client.post(
            reverse("edit_motion", args=[motion.pk]),
            {"round_selection": "inround_1", "motion_text": ""},
            follow=True,
        )
        self.assertEqual(missing_text_response.status_code, 200)
        self.assertIn(
            "Motion text is required.",
            missing_text_response.content.decode(),
        )

        missing_round_response = self.client.post(
            reverse("edit_motion", args=[motion.pk]),
            {"motion_text": "Updated motion"},
            follow=True,
        )
        self.assertEqual(missing_round_response.status_code, 200)
        self.assertIn(
            "Round selection is required.",
            missing_round_response.content.decode(),
        )

        invalid_round_response = self.client.post(
            reverse("edit_motion", args=[motion.pk]),
            {"round_selection": "invalid_round", "motion_text": "Updated motion"},
            follow=True,
        )
        self.assertEqual(invalid_round_response.status_code, 200)
        self.assertIn(
            "Invalid round selection.",
            invalid_round_response.content.decode(),
        )

        with patch(
            "mittab.apps.tab.views.motion_views.Motion.full_clean",
            side_effect=RuntimeError("boom"),
        ):
            exception_response = self.client.post(
                reverse("edit_motion", args=[motion.pk]),
                {"round_selection": "inround_1", "motion_text": "Updated motion"},
                follow=True,
            )
        self.assertEqual(exception_response.status_code, 200)
        self.assertIn(
            "Unexpected error updating motion. Please try again.",
            exception_response.content.decode(),
        )

    def test_edit_motion_updates_out_round_and_delete_post(self):
        motion = Motion.objects.create(round_number=1, motion_text="Original motion")

        with patch(
            "mittab.apps.tab.views.motion_views.invalidate_public_motions_cache"
        ) as invalidate_cache:
            response = self.client.post(
                reverse("edit_motion", args=[motion.pk]),
                {
                    "round_selection": "outround_0_4",
                    "info_slide": "Updated info",
                    "motion_text": "Updated for varsity semis",
                },
                follow=True,
            )
        self.assertEqual(response.status_code, 200)
        motion.refresh_from_db()
        self.assertEqual(motion.round_number, None)
        self.assertEqual(motion.outround_type, Motion.VARSITY)
        self.assertEqual(motion.num_teams, 4)
        self.assertEqual(motion.info_slide, "Updated info")
        invalidate_cache.assert_called_once()

        delete_response = self.client.post(
            reverse("delete_motion", args=[motion.pk]),
            follow=True,
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(Motion.objects.filter(pk=motion.pk).count(), 0)

    def test_toggle_and_bulk_publish_actions(self):
        motion_one = Motion.objects.create(round_number=1, motion_text="Motion 1")
        motion_two = Motion.objects.create(round_number=2, motion_text="Motion 2")
        self.assertEqual(Motion.objects.count(), 2)
        self.assertFalse(motion_one.is_published)
        self.assertFalse(motion_two.is_published)

        toggle_response = self.client.post(
            reverse("toggle_motion_published", args=[motion_one.pk]),
            follow=True,
        )
        self.assertEqual(toggle_response.status_code, 200)
        motion_one.refresh_from_db()
        self.assertTrue(motion_one.is_published)

        second_toggle_response = self.client.post(
            reverse("toggle_motion_published", args=[motion_one.pk]),
            follow=True,
        )
        self.assertEqual(second_toggle_response.status_code, 200)
        motion_one.refresh_from_db()
        self.assertFalse(motion_one.is_published)

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
        Motion.objects.create(
            outround_type=Motion.NOVICE,
            num_teams=4,
            motion_text="Novice semis motion",
            is_published=True,
        )

        public_client = Client()
        response = public_client.get(reverse("public_motions"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Published prelim motion", content)
        self.assertIn("Varsity quarters motion", content)
        self.assertIn("Novice semis motion", content)
        self.assertNotIn("Draft prelim motion", content)
        self.assertIn("Preliminary Rounds", content)
        self.assertIn("Varsity Elimination Rounds", content)
        self.assertIn("Novice Elimination Rounds", content)

        TabSettings.set("motions_enabled", 0)
        caches["public"].clear()
        disabled_response = public_client.get(reverse("public_motions"))
        self.assertEqual(disabled_response.status_code, 302)
        self.assertEqual(disabled_response.url, reverse("public_access_error"))

    def test_public_motions_empty_state(self):
        public_client = Client()
        response = public_client.get(reverse("public_motions"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("No Motions Available", content)
        self.assertIn("Motions will appear here once they are released", content)

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
