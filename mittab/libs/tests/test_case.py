import time

from django.test import LiveServerTestCase
from selenium import webdriver
from splinter import Browser

class BaseWebTestCase(LiveServerTestCase):
    """
    Abstract class to handle logic for web tests
    """
    username = 'tab'
    password = 'password'
    wait_seconds = 3.0

    def setUp(self):
        chrome_options = webdriver.ChromeOptions()
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--start-maximized")
        chrome_options.add_argument("--no-sandbox")
        self.browser = Browser('chrome', headless=True, wait_time=10, options=chrome_options)
        super(BaseWebTestCase, self).setUp()

    def tearDown(self):
        self.browser.quit()
        super(BaseWebTestCase, self).tearDown()

    def _post_teardown(self):
        try:
            super(BaseWebTestCase, self)._post_teardown()
        except Exception as e:
            import traceback
            traceback.print_exc()
            print("Ignoring exception in post-teardown")

    def _wait(self):
        time.sleep(self.wait_seconds)

    def _login(self):
        self._visit("")
        self.browser.fill('username', self.username)
        self.browser.fill('password', self.password)
        self.browser.find_by_text('Sign in').first.click()

        assert self.browser.is_text_present('Home')
        assert not self.browser.is_text_present('Sign in')

    def _go_home(self):
        self.browser.click_link_by_text('Home')
        self._wait()

    def _setup_confirm(self):
        """
        First part of work-around to let phantomjs accept confirmation dialogs
        http://stackoverflow.com/questions/19903146/confirm-alert-window-in-phantom-js
        """
        js_confirm = 'window.confirm = function() { return true }'
        self.browser.execute_script(js_confirm)

    def _accept_confirm(self):
        """
        Second part of work-around to let phantomjs accept confirmation dialogs
        MUST call self._setup_confirm() for this to work
        """
        self.browser.execute_script('return window.confirm')

    def _visit(self, path):
        path = self.live_server_url + path
        self.browser.visit(path)
        self._wait()
