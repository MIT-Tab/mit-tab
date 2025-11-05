import pytest
from django.test import TestCase, Client
from django.urls import reverse
from nplusone.core import profiler

from mittab.apps.tab.models import Round


@pytest.mark.django_db(transaction=True)
class TestStatsViews(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        self.client = Client()
        # Log in as admin user for permission-required views
        self.client.login(username="tab", password="password")

    def test_stats_content(self):
        """Test that stats views render correctly with expected content."""
        # Define views to test with their expected content checks
        # Format: (url_name, url_args, content_checks)
        views_to_test = [
            ("round_stats", None, self._check_round_stats_content),
        ]

        for url_name, url_args, content_checker in views_to_test:
            if url_args:
                url = reverse(url_name, args=url_args)
            else:
                url = reverse(url_name)

            response = self.client.get(url)
            self.assertEqual(
                response.status_code, 200,
                f"Failed to render {url_name}, got status {response.status_code}"
            )

            # Run the content checker function
            content_checker(response, url_name)

    def _check_round_stats_content(self, response, url_name):
        """Verify round stats page has correct statistics."""
        content = response.content.decode()

        # Basic structure checks
        self.assertIn("Round Statistics", content,
            f"Page title not found in {url_name}")

        # Check that we have actual rounds with data
        if Round.objects.exists():
            # Should show tournament stats
            self.assertIn("Tournament Overview", content,
                f"Tournament overview section not found in {url_name}")

    def test_n_plus_one(self):
        """Test that stats views don't have N+1 query problems."""
        # Define views to test for N+1 queries
        # Format: ((url_name,), url_args)
        views_to_test = [
            (("round_stats",), None),
            # Add more views here as they are created
            # Example: (("another_stats_view",), None),
            # Example: (("stats_by_round",), [1]),
        ]

        for view_name, url_args in views_to_test:
            with profiler.Profiler():
                if url_args:
                    response = self.client.get(reverse(*view_name, args=url_args))
                else:
                    response = self.client.get(reverse(*view_name))

                self.assertEqual(
                    response.status_code, 200,
                    f"Failed to render {view_name[0]}, "
                    f"got status {response.status_code}"
                )
