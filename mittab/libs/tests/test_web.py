from django.test import LiveServerTestCase
from selenium import webdriver
from selenium.webdriver.common.keys import Keys

class RunningATournamentTestCase(LiveServerTestCase):
    fixtures = ['testing_db']

    def setUp(self):
        self.selenium = webdriver.Firefox()
        super(RunningATournamentTestCase, self).setUp()

    def tearDown(self):
        self.selenium.quit()
        super(RunningATournamentTestCase, self).tearDown()

    def test_tournament(self):
        selenium = self.selenium
        self._login()

    def _login(self):
        selenium = self.selenium
        selenium.get('http://127.0.0.1:8000/')
        selenium.find_element_by_id('username').send_keys('tab')
        selenium.find_element_by_id('password').send_keys('testing')
        selenium.find_element_by_class_name('form-horizontal').submit()
        assert 'Sign in' not in selenium.page_source
        assert 'Welcome to MIT Tab' in selenium.title

