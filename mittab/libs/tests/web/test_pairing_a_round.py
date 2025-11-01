import time
import pytest
from mittab.libs.tests.test_case import BaseWebTestCase


@pytest.mark.django_db
class PairingARoundTestCase(BaseWebTestCase):
    """
    Test pairing a round, assigning judges, and enterring ballots
    """

    pytestmark = pytest.mark.django_db(transaction=True)
    fixtures = ["testing_db"]

    def test_pairing_round(self):
        self._login()
        self._visit("/pairings/status")

        self.browser.find_by_xpath("//a[contains(normalize-space(), 'Prepare Next Round')]").first.click()
        self.browser.find_by_xpath("//*[@value='Pair This Round']").first.click()

        time.sleep(10)
        self.browser.find_by_xpath("//*[@id='assign-judges']").first.click()
        self._accept_confirm()

        assert self._wait_for_text("Round Status for Round 1")
        assert self._wait_for_text("Valid pairing")

        self.browser.find_by_xpath("//*[text()='Enter Ballot']").first.click()

        # Ranks dont correspond with winner
        self._enter_results(
            winner="OPP",
            pm={
                "first": True,
                "speaks": 26,
                "ranks": 1
            },
            mg={
                "first": False,
                "speaks": 26,
                "ranks": 2
            },
            lo={
                "first": True,
                "speaks": 26,
                "ranks": 3
            },
            mo={
                "first": False,
                "speaks": 26,
                "ranks": 4
            },
        )
        assert self._wait_for_text("Low Point Win!!")

        # Speaks dont correspond with winner
        self._enter_results(
            winner="GOV",
            pm={
                "first": True,
                "speaks": 26,
                "ranks": 1
            },
            mg={
                "first": False,
                "speaks": 25,
                "ranks": 4
            },
            lo={
                "first": True,
                "speaks": 26,
                "ranks": 2
            },
            mo={
                "first": False,
                "speaks": 26,
                "ranks": 3
            },
        )
        assert self._wait_for_text("Low Point Win!!")

        # Ranks dont correspond with speaks
        self._enter_results(
            winner="GOV",
            pm={
                "first": True,
                "speaks": 26,
                "ranks": 1
            },
            mg={
                "first": False,
                "speaks": 25,
                "ranks": 2
            },
            lo={
                "first": True,
                "speaks": 26,
                "ranks": 3
            },
            mo={
                "first": False,
                "speaks": 25,
                "ranks": 4
            },
        )
        assert self._wait_for_text(
            "These speaks are too high for the rank")

        # Invalid speaks
        self._enter_results(
            winner="GOV",
            pm={
                "first": True,
                "speaks": 55,
                "ranks": 1
            },
            mg={
                "first": False,
                "speaks": 26,
                "ranks": 2
            },
            lo={
                "first": True,
                "speaks": 26,
                "ranks": 3
            },
            mo={
                "first": False,
                "speaks": 26,
                "ranks": 4
            },
        )
        assert self._wait_for_text("invalid speaker score")

        # Correct ballot
        self._enter_results(
            winner="GOV",
            pm={
                "first": True,
                "speaks": 26,
                "ranks": 1
            },
            mg={
                "first": False,
                "speaks": 26,
                "ranks": 2
            },
            lo={
                "first": True,
                "speaks": 26,
                "ranks": 3
            },
            mo={
                "first": False,
                "speaks": 26,
                "ranks": 4
            },
        )

        assert self._wait_for_text("Result entered successfully")
        assert self._wait_for_text("GOV win")

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
        self._wait()
        self.browser.find_by_xpath(f"//option[text()='{results['winner']}']").first.click()

        positions = ["pm", "mg", "lo", "mo"]
        for position in positions:
            result_data = results[position]

            debater_name_index = 2 if result_data["first"] else 3
            debater_name_xpath = f'//select[@name="{position}_debater"]/option[{debater_name_index}]'
            self.browser.find_by_xpath(debater_name_xpath).first.click()

            if result_data.get("ranks"):
                self.browser.select(f"{position}_ranks", result_data["ranks"])
            if result_data.get("speaks"):
                self.browser.fill(f"{position}_speaks", result_data["speaks"])

        self.browser.find_by_xpath("//*[@value='Save']").first.click()
