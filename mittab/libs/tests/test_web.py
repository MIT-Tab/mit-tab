import time
from django.test import LiveServerTestCase
from django.contrib.auth.models import User
from selenium import webdriver
from selenium.webdriver.common.keys import Keys

class RunningATournamentTestCase(LiveServerTestCase):
    """
    Tests the 'happy path' of running a tournament via the web interface
    """
    username = 'tab'
    password = 'tab'
    fixtures = ['testing_db']

    def setUp(self):
        self.selenium = webdriver.Firefox()
        super(RunningATournamentTestCase, self).setUp()

    def tearDown(self):
        self.selenium.quit()
        super(RunningATournamentTestCase, self).tearDown()

    def test_tournament(self):
        """
        Test running through a tournament in its entirety
        """
        self._login()
        self._add_rooms()

    def _login(self):
        """
        Test that logging in with the tab password takes you home
        """
        self.selenium.get('http://127.0.0.1:8000/')
        self.selenium.find_element_by_id('username').send_keys(self.username)
        self.selenium.find_element_by_id('password').send_keys(self.password)
        self.selenium.find_element_by_class_name('form-horizontal').submit()

        time.sleep(1)
        is_logged_in = 'Sign in' not in self.selenium.page_source
        assert is_logged_in

    def _add_rooms(self):
        """
        Test entering rooms
        """
        for i in range(5):
            self._add_room("Room %s" % i, i)

    def _add_room(self, name, rank):
        """
        Test submitting the room form and viewing the room on the dashboard
        """
        self._go_home()

        self.selenium.find_element_by_link_text('Add Room').click()
        time.sleep(1)

        self._submit_form(name=name, rank=rank)

        msg = "Room [%s] has been successfully modified!(CREATED)" % name
        assert msg in self.selenium.page_source

        self._go_home()
        self.selenium.find_element_by_link_text(name).click()

        time.sleep(1)
        assert name in self.selenium.page_source
        assert str(rank) in self.selenium.page_source

    def _submit_form(self, **data):
        """
        Submits the generic form used for model data with the passed data.
        For more complex forms, (i.e. non-text data), fill that in before calling
        this method.
        """
        for key in data.keys():
            self.selenium.find_element_by_id("id_%s" % key).send_keys(data[key])
        self.selenium.find_element_by_class_name('dataEntryForm').submit()
        time.sleep(1)

    def _go_home(self):
        """
        Navigate to the dashboard using the navigation bar
        """
        self.selenium.find_element_by_link_text('Home').click()
        time.sleep(1)
