from mittab.libs.tests.test_case import BaseWebTestCase

class SettingUpATournamentTestCase(BaseWebTestCase):
    """
    Tests setting up a tournament by entering rooms, judges, schools, debaters,
    teams, and scratches through the web interface
    """
    fixtures = ['testing_empty']

    def test_tournament(self):
        self._login()
        self._add_rooms()
        self._add_schools()
        self._add_judges()
        self._add_debaters()
        self._add_teams()
        self._go_home()

        self.browser.click_link_by_partial_text('Team 0')
        self.browser.click_link_by_text('Scratches for Team 0')
        # first link is the invisible link in the banner
        self.browser.find_link_by_text('Add Scratch').last.click()

        self.browser.find_option_by_text('Team 0').click()
        self.browser.find_option_by_text('Judge 2').click()
        self.browser.find_option_by_text('Tab Scratch').click()
        self.browser.find_by_value('Submit').click()

        msg = "has been successfully modified!(CREATED)"
        assert self.browser.is_text_present(msg)

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
                debater_option.double_click()
                #self.browser.click_link_by_id('id_debaters_add_link')

            school_option = self.browser.find_option_by_text(school).first
            school_option.click()

            seed_option = self.browser.find_option_by_text('Unseeded').first
            seed_option.click()

        self._add_entity('Team', select_team_options, name=name)

    def _add_judge(self, name, rank, schools):
        def click_schools():
            self._wait()
            for school in schools:
                el = self.browser.find_option_by_text(school).first
                el.double_click()

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
