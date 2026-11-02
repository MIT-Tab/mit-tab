from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait

from mittab.apps.registration.models import Registration
from mittab.apps.tab.models import Debater, School, Team


@override_settings(WEBPACK_LOADER={"DEFAULT": {
    "STATS_FILE": str(Path(settings.BASE_DIR) / "webpack-stats.json"),
    "BUNDLE_DIR_NAME": "webpack_bundles/",
}})
class RegistrationStatusWebTestCase(StaticLiveServerTestCase):
    def setUp(self):
        super().setUp()
        school = School.objects.create(name="Status U", apda_id=123)
        registration = Registration.objects.create(
            school=school, email="contact@example.com"
        )
        self.path = reverse("registration_portal_edit", args=[registration.herokunator_code])
        team = Team.objects.create(
            name="Status U A", school=school, registration=registration, seed=Team.UNSEEDED
        )
        self.debaters = []
        for index, status in enumerate((Debater.VARSITY, Debater.NOVICE)):
            debater = Debater.objects.create(
                name=f"Speaker {index}", email=f"speaker{index}@example.com",
                school=school, apda_id=100 + index, novice_status=status,
            )
            team.debaters.add(debater)
            self.debaters.append(debater)
        roster = [
            {"id": 100, "name": "Speaker 0", "is_novice": True},
            {"id": 101, "name": "Speaker 1", "status": "Varsity"},
            {"id": 102, "name": "New Novice", "novice_status": 1},
            {"id": 103, "name": "New Varsity", "is_novice": False},
        ]
        for patcher in (
            mock.patch("mittab.apps.registration.services.fetch_remote_schools", return_value=[]),
            mock.patch("mittab.apps.registration.views.requests.get",
                       return_value=mock.Mock(status_code=200, json=lambda: roster)),
            mock.patch("mittab.apps.registration.services.fetch_private_debater_emails",
                       return_value={}),
            mock.patch("mittab.apps.registration.emails.EmailService"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=1920,1080")
        self.driver = webdriver.Chrome(options=options)
        self.addCleanup(self.driver.quit)
        self.wait = WebDriverWait(self.driver, 10)

    def _field(self, suffix, slot="one"):
        return self.driver.find_element(By.NAME, f"teams-0-debater_{slot}_{suffix}")

    def _wait_for_roster(self, option_count=5):
        self.wait.until(lambda _: all(
            self._field("name", slot).is_enabled()
            and len(Select(self._field("name", slot)).options) == option_count
            for slot in ("one", "two")
        ))

    def _open(self, option_count=5):
        self.driver.get(self.live_server_url + self.path)
        self._wait_for_roster(option_count)
        self.driver.find_element(By.CSS_SELECTOR, "[data-form='team'] [data-subform-toggle]").click()

    def _submit(self):
        self.driver.find_element(By.CSS_SELECTOR, "[data-registration-form] button[type=submit]").click()

    def test_saved_status_survives_roster_load_and_save(self):
        self._open()
        self.assertEqual(self._field("novice_status").get_attribute("value"), "0")
        self.assertEqual(self._field("novice_status", "two").get_attribute("value"), "1")
        Select(self._field("novice_status", "two")).select_by_value("0")
        self._submit()
        self.wait.until(lambda _: "saved=1" in self.driver.current_url)
        for debater in self.debaters:
            debater.refresh_from_db()
            self.assertEqual(debater.novice_status, Debater.VARSITY)
        self._open()
        self.assertEqual(self._field("novice_status", "two").get_attribute("value"), "0")

    def test_selecting_different_debater_uses_roster_default(self):
        self._open()
        Select(self._field("name")).select_by_visible_text("New Novice")
        self.assertEqual(self._field("novice_status").get_attribute("value"), "1")
        Select(self._field("name")).select_by_visible_text("New Varsity")
        self.assertEqual(self._field("novice_status").get_attribute("value"), "0")

    def test_validation_error_preserves_manual_status(self):
        self._open()
        Select(self._field("novice_status")).select_by_value("0")
        self.driver.find_element(By.NAME, "teams-0-name").clear()
        self._submit()
        self.wait.until(lambda _: self.driver.find_elements(
            By.CSS_SELECTOR, "[data-registration-bound]"
        ))
        self._wait_for_roster()
        self.assertEqual(self._field("novice_status").get_attribute("value"), "0")

    def test_draft_restores_manual_status(self):
        self.debaters[0].novice_status = Debater.NOVICE
        self.debaters[0].save()
        self._open()
        Select(self._field("novice_status")).select_by_value("0")
        # pagehide writes the draft immediately, without waiting for debounce.
        self.driver.execute_script("window.dispatchEvent(new Event('pagehide'))")
        self.driver.refresh()
        self._wait_for_roster()
        self.assertEqual(self._field("novice_status").get_attribute("value"), "0")

    def test_local_school_draft_restores_manual_status(self):
        school = self.debaters[0].school
        school.apda_id = -1
        school.save()
        self._open(option_count=2)
        Select(self._field("novice_status", "two")).select_by_value("0")
        self.driver.execute_script("window.dispatchEvent(new Event('pagehide'))")
        self.driver.refresh()
        self._wait_for_roster(option_count=2)
        self.assertEqual(self._field("novice_status", "two").get_attribute("value"), "0")
