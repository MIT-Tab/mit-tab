from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from mittab.apps.registration.models import (
    Registration,
    RegistrationConfirmationEmailLog,
)
from mittab.apps.tab.models import School


@pytest.mark.django_db(transaction=True)
class TestRegistrationEmailManagement(TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            username="registration-email-user",
            password="testpass123",
            email="test@example.com",
        )
        self.client.login(
            username="registration-email-user",
            password="testpass123",
        )

    @mock.patch("mittab.apps.tab.views.judge_views.EmailService")
    def test_email_management_resends_registration_confirmations(self, email_service):
        email_service.return_value.send_bulk.return_value = 1
        school = School.objects.create(name="Registration Email U")
        registration = Registration.objects.create(
            school=school,
            email="registration@example.com",
        )
        for index in range(3):
            Registration.objects.create(
                school=school,
                email=f"other-registration-{index}@example.com",
            )

        url = reverse("email_management")
        with CaptureQueriesContext(connection) as preview_queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Registration Confirmations", content)
        self.assertIn("Registration Email U", content)
        self.assertRegex(
            content,
            rf'name="registration_ids" value="{registration.id}" checked',
        )

        with CaptureQueriesContext(connection) as send_queries:
            response = self.client.post(url, {
                "email_action": "registration_confirmations",
                "registration_ids": [str(registration.id)],
            })

        self.assertEqual(response.status_code, 302)
        for queries in (preview_queries, send_queries):
            for table in ("registration_registration", "registration_registrationlink"):
                table_name = connection.ops.quote_name(table)
                self.assertEqual(sum(
                    f"FROM {table_name} " in query["sql"] for query in queries
                ), 1, table)
        email_service.return_value.send_bulk.assert_called_once()
        log = RegistrationConfirmationEmailLog.objects.get(
            registration=registration,
        )
        self.assertEqual(log.email, "registration@example.com")
        self.assertTrue(log.successful)
        sent_request = email_service.return_value.send_bulk.call_args.args[0][0]
        self.assertEqual(log.email_payload, sent_request.snapshot)
