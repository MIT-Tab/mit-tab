import time
from django.test import LiveServerTestCase
from django.contrib.auth.models import User
from selenium import webdriver
from selenium.webdriver.common.keys import Keys

class RunningATournamentTestCase(LiveServerTestCase):
    username = 'tab'
    password = 'tab'

    def setUp(self):
        self.selenium = webdriver.Firefox()
        super(RunningATournamentTestCase, self).setUp()

    def tearDown(self):
        self.selenium.quit()
        super(RunningATournamentTestCase, self).tearDown()

    def test_tournament(self):
        self._login()

    def _login(self):
        self.selenium.get('http://127.0.0.1:8000/')
        self.selenium.find_element_by_id('username').send_keys(self.username)
        self.selenium.find_element_by_id('password').send_keys(self.password)
        self.selenium.find_element_by_class_name('form-horizontal').submit()

        time.sleep(1)
        is_logged_in = 'Sign in' not in self.selenium.page_source
        assert is_logged_in

