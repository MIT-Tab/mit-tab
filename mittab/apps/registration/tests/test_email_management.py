from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
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

        response = self.client.get(reverse("email_management"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Registration Confirmations", content)
        self.assertIn("Registration Email U", content)
        self.assertRegex(
            content,
            rf'name="registration_ids" value="{registration.id}" checked',
        )

        response = self.client.post(
            reverse("email_management"),
            {
                "email_action": "registration_confirmations",
                "registration_ids": [str(registration.id)],
            },
        )

        self.assertEqual(response.status_code, 302)
        email_service.return_value.send_bulk.assert_called_once()
        log = RegistrationConfirmationEmailLog.objects.get(
            registration=registration,
        )
        self.assertEqual(log.email, "registration@example.com")
        self.assertTrue(log.successful)
