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
        self._add_schools()

    def _login(self):
        """
        Test that logging in with the tab password takes you home
        """
        self.selenium.get(self.live_server_url)
        self.selenium.find_element_by_id('username').send_keys(self.username)
        self.selenium.find_element_by_id('password').send_keys(self.password)
        self.selenium.find_element_by_class_name('form-horizontal').submit()

        self._sleep()
        is_logged_in = 'Sign in' not in self.selenium.page_source
        assert is_logged_in

    def _add_rooms(self):
        """
        Test entering rooms
        """
        for i in range(5):
            self._add_room("Room %s" % i, i)
            self._sleep(2)

    def _add_schools(self):
        """
        Test entering schools
        """
        for i in range(5):
            self._add_school("School %s" % i)

    def _add_school(self, name):
        """
        Test submitting the school form and viewing the school on the dashboard
        """
        self._add_entity('School', name=name)

    def _add_room(self, name, rank):
        """
        Test submitting the room form and viewing the room on the dashboard
        """
        self._add_entity('Room', name=name, rank=rank)

    def _add_entity(self, entity_name, **data):
        """
        Test adding an entity (i.e. School, Room, etc.) via the dashboard and verify
        that it was successfully added

         - The model must have a name attribute used to determine links
         - The name cannot be shared with any other entity
         - Does not work with more complex form submission
        """
        self._go_home()

        self.selenium.find_element_by_link_text("Add %s" % entity_name).click()
        self._sleep()
        self._submit_form(**data)

        msg = "%s [%s] has been successfully modified!(CREATED)" % (entity_name, data['name'])
        assert msg in self.selenium.page_source

        self._go_home()
        self.selenium.find_element_by_link_text(data['name']).click()
        self._sleep()

        for key in data:
            assert str(data[key]) in self.selenium.page_source


    def _submit_form(self, **data):
        """
        Submits the generic form used for model data with the passed data.
        For more complex forms, (i.e. non-text data), fill that in before calling
        this method.
        """
        for key in data:
            self.selenium.find_element_by_id("id_%s" % key).send_keys(data[key])
        self.selenium.find_element_by_class_name('dataEntryForm').submit()
        self._sleep(10)

    def _go_home(self, sleep=0):
        """
        Navigate to the dashboard using the navigation bar
        """
        self.selenium.find_element_by_link_text('Home').click()
        self._sleep(sleep)

    def _sleep(self, additional=0):
        time.sleep(2 + additional)
