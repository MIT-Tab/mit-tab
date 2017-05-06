import time
from splinter import Browser
from django.test import LiveServerTestCase
from django.contrib.auth.models import User
from django.core.management import call_command

class RunningATournamentTestCase(LiveServerTestCase):
    """
    Tests the 'happy path' of running a tournament via the web interface
    """
    fixtures = ['testing_empty']
    username = 'tab'
    password = 'tab'

    def setUp(self):
        self.selenium = Browser('phantomjs', wait_time=10)
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
        self._add_judges()
        self._add_debaters()

    def _login(self):
        """
        Test that logging in with the tab password takes you home
        """
        self.selenium.visit(self.live_server_url)
        self.selenium.fill('username', self.username)
        self.selenium.fill('password', self.password)
        self.selenium.find_by_text('Sign in').first.click()

        assert self.selenium.is_text_present('Home')
        assert not self.selenium.is_text_present('Sign in')

    def _add_debaters(self):
        for i in range(4):
            self._add_debater("Debater %s" % (i * 2), False)
            self._add_debater("Debater %s" % (i * 2 + 1), True)

    def _add_judges(self):
        for i in range(5):
            self._add_judge("Judge %s" % i, i, ["School %s" % i])

    def _add_rooms(self):
        """
        Test entering rooms
        """
        for i in range(5):
            self._add_room("Room %s" % i, i)

    def _add_schools(self):
        """
        Test entering schools
        """
        for i in range(5):
            self._add_school("School %s" % i)

    def _add_judge(self, name, rank, schools):
        """
        Test submitting a judge
         - The schools param is an array of school names the judge is affiliated
           with
        """
        def click_schools():
            for school in schools:
                el = self.selenium.find_option_by_text(school).first
                el.click()

        self._add_entity('Judge', click_schools, name=name, rank=rank)


    def _add_debater(self, name, varsity):
        def select_varsity_status():
            val = '0' if varsity else '1'
            self.selenium.select('novice_status', val)

        self._add_entity('Debater', select_varsity_status, name=name)

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

    def _add_entity(self, entity_name, custom_form_logic=None, **data):
        """
        Test adding an entity (i.e. School, Room, etc.) via the dashboard and verify
        that it was successfully added

         - The model must have a name attribute used to determine links
         - The name cannot be shared with any other entity
         - Only submits data for plain text/number fields. Pass in a lambda for
           the custom_form_logic parameter to fill in any additional fields
        """
        self._go_home()

        self.selenium.click_link_by_text("Add %s" % entity_name)
        if custom_form_logic:
            custom_form_logic()
        self._submit_form(**data)

        msg = "%s [%s] has been successfully modified!(CREATED)" % (entity_name, data['name'])
        assert self.selenium.is_text_present(msg)

        self._go_home()
        self.selenium.click_link_by_partial_text(data['name'])

        for key in data:
            assert self.selenium.is_text_present(str(data[key]))


    def _submit_form(self, **data):
        """
        Submits the generic form used for model data with the passed data.
        For more complex forms, (i.e. non-text data), fill that in before calling
        this method.
        """
        for key in data:
            self.selenium.fill(key, data[key])
        self.selenium.find_by_value('Submit Changes').first.click()

    def _go_home(self):
        """
        Navigate to the dashboard using the navigation bar
        """
        self.selenium.click_link_by_text('Home')

