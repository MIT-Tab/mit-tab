from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from mittab.apps.tab.models import CheckIn, Judge, TabSettings


@override_settings(WEBPACK_LOADER={"DEFAULT": {
    "STATS_FILE": str(Path(settings.BASE_DIR) / "webpack-stats.json"),
    "BUNDLE_DIR_NAME": "webpack_bundles/",
}})
class BatchCheckinWebTestCase(StaticLiveServerTestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=1920,1080")
        self.driver = webdriver.Chrome(options=options)
        self.addCleanup(self.driver.quit)
        self.wait = WebDriverWait(self.driver, 10)
        self.judge = Judge.objects.create(name="Batch Browser Judge", rank=Decimal("2"))
        user = get_user_model().objects.create_superuser(
            username="batch-browser", password="password", email="batch@example.com"
        )
        self.client.force_login(user)
        self.driver.get(self.live_server_url + "/public/login/")
        self.driver.add_cookie({
            "name": settings.SESSION_COOKIE_NAME,
            "value": self.client.cookies[settings.SESSION_COOKIE_NAME].value,
        })
        self.driver.get(self.live_server_url + reverse("batch_checkin"))
        self.driver.find_element(By.ID, "judge-tab").click()
        self.wait.until(lambda _: self.driver.find_element(
            By.CSS_SELECTOR, "#judge.show"
        ))

    def _toggle(self, round_number):
        return self.driver.find_element(
            By.ID, f"toggle-judge-{self.judge.id}-{round_number}"
        )

    def _click_toggle(self, round_number):
        self.driver.find_element(
            By.CSS_SELECTOR,
            f"label[for='toggle-judge-{self.judge.id}-{round_number}']",
        ).click()

    def _status(self):
        return self.driver.find_element(By.CSS_SELECTOR, "#judge .checkin-status").text

    def _hold_requests(self):
        self.driver.execute_script("""
            const originalOpen = XMLHttpRequest.prototype.open;
            const originalSend = XMLHttpRequest.prototype.send;
            window.checkinRequests = [];
            XMLHttpRequest.prototype.open = function(method, url, ...args) {
                this.isCheckin = url === '/bulk_check_in/';
                return originalOpen.call(this, method, url, ...args);
            };
            XMLHttpRequest.prototype.send = function(body) {
                if (this.isCheckin) {
                    window.checkinRequests.push({
                        xhr: this, send: () => originalSend.call(this, body)
                    });
                } else {
                    originalSend.call(this, body);
                }
            };
        """)

    def test_slow_request_waits_for_confirmation_and_prevents_overlapping_saves(self):
        self._hold_requests()
        count = self.driver.find_element(
            By.CSS_SELECTOR, "#judge .checkin-count[data-round-index='1']"
        )
        initial_count = int(count.text)
        self._click_toggle(1)
        self.assertEqual(self._status(), "Saving check-in changes…")
        self.assertFalse(self._toggle(1).is_selected())
        self.assertFalse(self._toggle(1).is_enabled())
        self.assertEqual(int(count.text), initial_count)
        self.assertFalse(CheckIn.objects.filter(judge=self.judge).exists())

        self._click_toggle(2)
        self.assertEqual(self.driver.execute_script(
            "return window.checkinRequests.length"
        ), 1)
        self.driver.execute_script("window.checkinRequests[0].send()")
        self.wait.until(lambda _: self._status() == "Check-in changes saved.")
        self.assertTrue(self._toggle(1).is_selected())
        self.assertTrue(self._toggle(1).is_enabled())
        self.assertEqual(int(count.text), initial_count + 1)
        self.assertEqual(list(CheckIn.objects.filter(judge=self.judge).values_list(
            "round_number", flat=True
        )), [1])

        self.driver.refresh()
        self.assertTrue(self._toggle(1).is_selected())
        self.assertFalse(self._toggle(0).is_selected())

    def test_network_failure_keeps_confirmed_state_and_shows_error(self):
        self.driver.execute_cdp_cmd("Network.enable", {})
        self.driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": ["*bulk_check_in*"]})
        self._click_toggle(1)
        self.wait.until(lambda _: "could not be confirmed" in self._status())
        self.assertFalse(self._toggle(1).is_selected())
        self.assertTrue(self._toggle(1).is_enabled())
        self.assertFalse(CheckIn.objects.filter(judge=self.judge).exists())

    def test_bulk_buttons_and_drag_save_the_visible_rounds(self):
        for scope, action, expected_rounds in (
            ("column", "check_in", [1]),
            ("column", "check_out", []),
            ("row", "check_in", list(range(TabSettings.get("tot_rounds") + 1))),
            ("row", "check_out", []),
        ):
            if scope == "column":
                selector = f"#judge th .bulk-toggle[data-round='1'][data-action='{action}']"
                button = self.driver.find_element(By.CSS_SELECTOR, selector)
            else:
                row = self._toggle(1).find_element(By.XPATH, "ancestor::tr")
                button = row.find_element(
                    By.CSS_SELECTOR, f".bulk-toggle[data-action='{action}']"
                )
            button.click()
            self.wait.until(lambda _: self._status() == "Check-in changes saved.")
            self.assertEqual(list(CheckIn.objects.filter(judge=self.judge).order_by(
                "round_number"
            ).values_list("round_number", flat=True)), expected_rounds)

        labels = [self.driver.find_element(
            By.CSS_SELECTOR, f"label[for='toggle-judge-{self.judge.id}-{rn}']"
        ) for rn in (1, 2)]
        ActionChains(self.driver).move_to_element(labels[0]).click_and_hold().move_to_element(
            labels[1]
        ).release().perform()
        self.wait.until(lambda _: self._status() == "Check-in changes saved.")
        self.assertEqual(list(CheckIn.objects.filter(judge=self.judge).order_by(
            "round_number"
        ).values_list("round_number", flat=True)), [1, 2])

    def test_expired_session_html_does_not_show_success(self):
        self.client.logout()
        self._click_toggle(1)
        self.wait.until(lambda _: "could not be confirmed" in self._status())
        self.assertFalse(self._toggle(1).is_selected())
        self.assertTrue(self._toggle(1).is_enabled())
        self.assertFalse(CheckIn.objects.filter(judge=self.judge).exists())
