from splinter import Browser
from django.test import LiveServerTestCase

class BaseWebTestCase(LiveServerTestCase):
    """
    Abstract class to handle logic for web tests
    """
    username = 'tab'
    password = 'tab'

    def setUp(self):
        self.browser = Browser('phantomjs', wait_time=10)
        super(BaseWebTestCase, self).setUp()

    def tearDown(self):
        self.browser.quit()
        super(BaseWebTestCase, self).tearDown()

    def _login(self):
        self.browser.visit(self.live_server_url)
        self.browser.fill('username', self.username)
        self.browser.fill('password', self.password)
        self.browser.find_by_text('Sign in').first.click()

    def _go_home(self):
        self.browser.click_link_by_text('Home')

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


class SettingUpATournamentTestCase(BaseWebTestCase):
    """
    Tests setting up a tournament by entering rooms, judges, schools, debaters
    and teams through the web interface
    """
    fixtures = ['testing_empty']

    def test_tournament(self):
        print "Logging in..."
        self._login()
        print "Adding rooms..."
        self._add_rooms()
        print "Adding schools..."
        self._add_schools()
        print "Adding judges..."
        self._add_judges()
        print "Adding debaters..."
        self._add_debaters()
        print "Adding teams..."
        self._add_teams()

        assert self.browser.is_text_present('Home')
        assert not self.browser.is_text_present('Sign in')

    def _add_teams(self):
        for i in range(4):
            debaters = ["Debater %s" % (i * 2), "Debater %s" % (i * 2 + 1)]
            self._add_team("Team %s" % i, debaters, "School %s" % i)

    def _add_debaters(self):
        for i in range(4):
            self._add_debater("Debater %s" % (i * 2), False)
            self._add_debater("Debater %s" % (i * 2 + 1), True)

    def _add_judges(self):
        for i in range(5):
            self._add_judge("Judge %s" % i, i, ["School %s" % i])

    def _add_rooms(self):
        for i in range(5):
            self._add_room("Room %s" % i, i)

    def _add_schools(self):
        for i in range(5):
            self._add_school("School %s" % i)

    def _add_team(self, name, debaters, school):
        def select_team_options():
            for debater in debaters:
                debater_option = self.browser.find_option_by_text(debater).first
                debater_option.click()

            school_option = self.browser.find_option_by_text(school).first
            school_option.click()

            seed_option = self.browser.find_option_by_text('Unseeded').first
            seed_option.click()

        self._add_entity('Team', select_team_options, name=name)

    def _add_judge(self, name, rank, schools):
        def click_schools():
            for school in schools:
                el = self.browser.find_option_by_text(school).first
                el.click()

        self._add_entity('Judge', click_schools, name=name, rank=rank)
        for i in range(5):
            self.browser.check("checkin_%s" % i)

        self.browser.find_by_value('Submit Changes').first.click()
        msg = "Judge [%s] has been successfully modified!(EDIT)" % name
        assert self.browser.is_text_present(msg)


    def _add_debater(self, name, varsity):
        def select_varsity_status():
            val = '0' if varsity else '1'
            self.browser.select('novice_status', val)

        self._add_entity('Debater', select_varsity_status, name=name)

    def _add_school(self, name):
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

        self.browser.click_link_by_text("Add %s" % entity_name)
        if custom_form_logic:
            custom_form_logic()
        self._submit_form(**data)

        msg = "%s [%s] has been successfully modified!(CREATED)" % (entity_name, data['name'])
        assert self.browser.is_text_present(msg)

        self._go_home()
        self.browser.click_link_by_partial_text(data['name'])

        for key in data:
            assert self.browser.is_text_present(str(data[key]))

    def _submit_form(self, **data):
        """
        Submits the generic form used for model data with the passed data.
        For more complex forms, (i.e. non-text data), fill that in before calling
        this method.
        """
        for key in data:
            self.browser.fill(key, data[key])
        self.browser.find_by_value('Submit Changes').first.click()


class PairingARoundTestCase(BaseWebTestCase):
    """
    Test pairing a round, assigning judges, entering ballots and restoring from
    the back-up
    """
    fixtures = ['testing_db']

    def test_pairing_round(self):
        self._login()
        self.browser.visit("%s/pairings/status" % self.live_server_url)

        self.browser.click_link_by_text('Prepare Next Round')
        self.browser.find_by_value('Pair This Round').first.click()

        self._setup_confirm()
        self.browser.find_by_text('Assign Judges').first.click()
        self._accept_confirm()

        assert self.browser.is_text_present('Round Status for Round: 1')
        assert self.browser.is_text_present('We have a valid Pairing')

        self.browser.find_by_text('Enter Ballot').first.click()

        self._enter_results(winner='GOV',
                pm={ 'first': True, 'speaks': 26, 'ranks': 1 },
                mg={ 'first': False, 'speaks': 26, 'ranks': 2 },
                lo={ 'first': True, 'speaks': 26, 'ranks': 3 },
                mo={ 'first': False, 'speaks': 26, 'ranks': 4 })

        assert self.browser.is_text_present('has been successfully modified')
        self.browser.click_link_by_text('Go To Ballot Entry')
        assert self.browser.is_text_present('GOV win')


    def _enter_results(self, **results):
        """
        Enters results for a ballot, where results are a dict in the following
        format:

        {
            "winner": String
            "pm": { "first": {Bool}, "speaks": {Float}, "ranks": {Int} }
            "mg": { "first": {Bool}, "speaks": {Float}, "ranks": {Int} }
            "lo": { "first": {Bool}, "speaks": {Float}, "ranks": {Int} }
            "mo": { "first": {Bool}, "speaks": {Float}, "ranks": {Int} }
        }

        the "first" option is true if the debater to select is the first debater
        in the drop-down
        """
        self.browser.find_option_by_text(results['winner']).first.click()

        positions = ['pm', 'mg', 'lo', 'mo']
        for position in positions:
            result_data = results[position]

            debater_name_index = 2 if result_data['first'] else 3
            debater_name_xpath = '//select[@name="%s_debater"]/option[%s]' % (position, debater_name_index)
            self.browser.find_by_xpath(debater_name_xpath).first.click()

            self.browser.select("%s_ranks" % position, result_data['ranks'])
            self.browser.fill("%s_speaks" % position, result_data['speaks'])

        self.browser.find_by_value('Submit Changes').first.click()
