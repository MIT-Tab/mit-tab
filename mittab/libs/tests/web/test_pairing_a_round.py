from mittab.libs.tests.test_case import BaseWebTestCase


class PairingARoundTestCase(BaseWebTestCase):
    """
    Test pairing a round, assigning judges, and enterring ballots
    """

    fixtures = ["testing_db"]

    def test_pairing_round(self):
        self._login()
        self._visit("/pairings/status")

        self.browser.click_link_by_partial_text("Prepare Next Round")
        self.browser.find_by_value("Pair This Round").first.click()

        self._setup_confirm()
        self.browser.find_by_id("assign-judges").first.click()
        self._accept_confirm()

        assert self.browser.is_text_present("Round Status for Round 1")
        assert self.browser.is_text_present("Valid pairing")

        self.browser.find_by_text("Enter Ballot").first.click()

        # Ranks dont correspond with winner
        self._enter_results(
            winner="OPP",
            pm={"first": True, "speaks": 26, "ranks": 1},
            mg={"first": False, "speaks": 26, "ranks": 2},
            lo={"first": True, "speaks": 26, "ranks": 3},
            mo={"first": False, "speaks": 26, "ranks": 4},
        )
        assert self.browser.is_text_present("Low Point Win!!")

        # Speaks dont correspond with winner
        self._enter_results(
            winner="GOV",
            pm={"first": True, "speaks": 26, "ranks": 1},
            mg={"first": False, "speaks": 25, "ranks": 4},
            lo={"first": True, "speaks": 26, "ranks": 2},
            mo={"first": False, "speaks": 26, "ranks": 3},
        )
        assert self.browser.is_text_present("Low Point Win!!")

        # Ranks dont correspond with speaks
        self._enter_results(
            winner="GOV",
            pm={"first": True, "speaks": 26, "ranks": 1},
            mg={"first": False, "speaks": 25, "ranks": 2},
            lo={"first": True, "speaks": 26, "ranks": 3},
            mo={"first": False, "speaks": 25, "ranks": 4},
        )
        assert self.browser.is_text_present("These speaks are too high for the rank")

        # Invalid speaks
        self._enter_results(
            winner="GOV",
            pm={"first": True, "speaks": 55, "ranks": 1},
            mg={"first": False, "speaks": 26, "ranks": 2},
            lo={"first": True, "speaks": 26, "ranks": 3},
            mo={"first": False, "speaks": 26, "ranks": 4},
        )
        assert self.browser.is_text_present("invalid speaker score")

        # Correct ballot
        self._enter_results(
            winner="GOV",
            pm={"first": True, "speaks": 26, "ranks": 1},
            mg={"first": False, "speaks": 26, "ranks": 2},
            lo={"first": True, "speaks": 26, "ranks": 3},
            mo={"first": False, "speaks": 26, "ranks": 4},
        )

        assert self.browser.is_text_present("Result entered successfully")
        assert self.browser.is_text_present("GOV win")

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
        self.browser.find_option_by_text(results["winner"]).first.click()

        positions = ["pm", "mg", "lo", "mo"]
        for position in positions:
            result_data = results[position]

            debater_name_index = 2 if result_data["first"] else 3
            debater_name_xpath = '//select[@name="%s_debater"]/option[%s]' % (
                position,
                debater_name_index,
            )
            self.browser.find_by_xpath(debater_name_xpath).first.click()

            if result_data.get("ranks"):
                self.browser.select("%s_ranks" % position, result_data["ranks"])
            if result_data.get("speaks"):
                self.browser.fill("%s_speaks" % position, result_data["speaks"])

        self.browser.find_by_value("Save").first.click()
