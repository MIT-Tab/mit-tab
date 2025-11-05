import os
import time
import tempfile

from django.test import LiveServerTestCase
from django.core.cache import cache
from django.urls import reverse
from selenium import webdriver
from selenium.common.exceptions import NoAlertPresentException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from splinter import Browser

from mittab.apps.tab.models import TabSettings


class BaseWebTestCase(LiveServerTestCase):
    """
    Abstract class to handle logic for web tests
    """

    username = "tab"
    password = "password"
    wait_seconds = 1

    def setUp(self):
        if os.environ.get("TEST_BROWSER", "chrome") == "chrome":
            chrome_options = webdriver.ChromeOptions()
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--start-maximized")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--incognito")
            temp_dir = tempfile.mkdtemp()
            chrome_options.add_argument("--user-data-dir=" + temp_dir)
            self.browser = Browser("chrome",
                                   headless=False,
                                   wait_time=30,
                                   options=chrome_options)
        else:
            self.browser = Browser("firefox",
                                   headless=False,
                                   wait_time=30)
        self.browser.driver.set_page_load_timeout(240)
        TabSettings.set("cur_round", 1)
        super(BaseWebTestCase, self).setUp()

    def tearDown(self):
        self.browser.quit()
        super(BaseWebTestCase, self).tearDown()
        cache.clear()

    def _wait(self):
        time.sleep(self.wait_seconds)

    def _login(self):
        self._visit(reverse("tab_login"))
        self.browser.fill("username", self.username)
        self.browser.fill("password", self.password)
        self.browser.find_by_text("Sign in").first.click()

        assert self._wait_for_text("Admin")  # checks that the nav is visible

    def _go_home(self):
        self._visit("/")

    def _accept_confirm(self):
        try:
            alert = self.browser.driver.switch_to.alert
            alert.accept()
        except NoAlertPresentException:
            pass

    def _visit(self, path):
        path = self.live_server_url + path
        self.browser.visit(path)
        self._wait()

    def _wait_for_text(self, text, timeout=5, retries=2):
        for attempt in range(retries):
            try:
                return WebDriverWait(self.browser.driver, timeout).until(
                    EC.text_to_be_present_in_element((By.TAG_NAME, "body"), text)
                )
            except Exception as e:
                if attempt < retries - 1:
                    continue
                print(e)
                return False
        return False
