import pytest
from mittab.libs.tests.test_case import BaseWebTestCase


@pytest.mark.django_db
class SettingUpATournamentTestCase(BaseWebTestCase):
    """
    Tests setting up a tournament by entering rooms, judges, schools, debaters,
    teams, and scratches through the web interface
    """

    pytestmark = pytest.mark.django_db(transaction=True)
    fixtures = ["testing_empty"]

    def test_tournament(self):
        self._login()
        self._add_rooms()
        self._add_schools()
        self._add_judges()
        self._add_debaters()
        self._add_teams()
        self._go_home()

        self.browser.find_by_xpath("//a[contains(text(), 'Team 0')]").first.click()
        self.browser.find_by_xpath("//*[text()='Scratches for Team 0']").first.click()

        self.browser.find_by_xpath("//*[text()='Add Scratch']").last.click()
        self.browser.find_by_xpath("//*[text()='Team 0']").first.click()
        self.browser.find_by_xpath("//*[text()='Judge 2']").first.click()
        self.browser.find_by_xpath("//*[text()='Tab Scratch']").first.click()
        self.browser.find_by_xpath("//*[@value='Submit']").first.click()

        msg = "Scratches created successfully"
        assert self._wait_for_text(msg)

    def _add_teams(self):
        for i in range(4):
            debaters = [f"Debater {i * 2}", f"Debater {i * 2 + 1}"]
            self._add_team(f"Team {i}", debaters, f"School {i}")

    def _add_debaters(self):
        for i in range(4):
            self._add_debater(f"Debater {i * 2}", False)
            self._add_debater(f"Debater {i * 2 + 1}", True)

        self._go_home()
        self.browser.find_by_xpath("//*[@id='debater-list-btn-list']").first.click()
        self._wait()

        for i in range(4):
            assert self._wait_for_text(f"Debater {i * 2}")
            assert self._wait_for_text(f"Debater {i * 2 + 1}")

    def _add_judges(self):
        for i in range(5):
            self._add_judge(f"Judge {i}", i, [f"School {i}"])

        self._go_home()
        self.browser.find_by_xpath("//*[@id='judge-list-btn-list']").first.click()
        self._wait()

        for i in range(5):
            assert self._wait_for_text(f"Judge {i}")

    def _add_rooms(self):
        for i in range(5):
            self._add_room(f"Room {i}", i)

        self._go_home()
        self.browser.find_by_xpath("//*[@id='room-list-btn-list']").first.click()
        self._wait()

        for i in range(5):
            assert self._wait_for_text(f"Room {i}")

    def _add_schools(self):
        for i in range(5):
            self._add_school(f"School {i}")

        self._go_home()
        self.browser.find_by_xpath("//*[@id='school-list-btn-list']").first.click()
        self._wait()

        for i in range(5):
            assert self._wait_for_text(f"School {i}")

    def _add_team(self, name, debaters, school):
        def select_team_options():
            for debater in debaters:
                self.browser.find_by_xpath(f"//*[text()='{debater}']").first.click()

            self.browser.find_by_xpath(f"//*[text()='{school}']").first.click()
            self.browser.find_by_xpath("//*[text()='Unseeded']").first.click()

        self._add_entity("Team", select_team_options, name=name)

    def _add_judge(self, name, rank, schools):
        def click_schools():
            for school in schools:
                self.browser.find_by_xpath(f"//*[text()='{school}']").first.click()

        self._add_entity("Judge", click_schools, name=name, rank=rank)
        for i in range(5):
            self.browser.check(f"checkin_{i}")

        self.browser.find_by_xpath("//*[@value='Save']").first.click()
        msg = f"Judge {name} updated successfully"
        assert self._wait_for_text(msg)

    def _add_debater(self, name, varsity):
        def select_varsity_status():
            val = "0" if varsity else "1"
            self.browser.select("novice_status", val)

        self._add_entity("Debater", select_varsity_status, name=name)

    def _add_school(self, name):
        self._add_entity("School", name=name)

    def _add_room(self, name, rank):
        """
        Test submitting the room form and viewing the room on the dashboard
        """
        self._add_entity("Room", name=name, rank=rank)

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
        self.browser.find_by_xpath(f"//*[@id='{entity_name.lower()}-list-btn-add']").first.click()

        if custom_form_logic:
            custom_form_logic()

        self._submit_form(**data)

        msg = f"{entity_name} {data['name']} created successfully"
        assert self._wait_for_text(msg)

        self._go_home()
        self.browser.find_by_xpath(f"//a[contains(text(), '{data['name']}')]").first.click()

        for key in data:
            assert self._wait_for_text(str(data[key]))

    def _submit_form(self, **data):
        """
        Submits the generic form used for model data with the passed data.
        For more complex forms, (i.e. non-text data), fill that in before calling
        this method.
        """
        for key, value in data.items():
            self.browser.fill(key, value)

        self.browser.find_by_xpath("//*[@value='Save']").first.click()
