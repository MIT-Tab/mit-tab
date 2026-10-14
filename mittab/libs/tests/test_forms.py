import pytest
from django.test import TestCase

from mittab.apps.tab.forms import EBallotForm, ResultEntryForm
from mittab.apps.tab.models import Judge, Round, RoundStats, TabSettings


def ballot_form_data(round_obj, **overrides):
    gov_debaters = list(round_obj.gov_team.debaters.all())
    opp_debaters = list(round_obj.opp_team.debaters.all())
    data = {
        "winner": Round.GOV,
        "round_instance": round_obj.id,
        "pm_debater": gov_debaters[0].id,
        "pm_speaks": "30",
        "pm_ranks": "1",
        "mg_debater": gov_debaters[-1].id,
        "mg_speaks": "29",
        "mg_ranks": "2",
        "lo_debater": opp_debaters[0].id,
        "lo_speaks": "28",
        "lo_ranks": "3",
        "mo_debater": opp_debaters[-1].id,
        "mo_speaks": "27",
        "mo_ranks": "4",
    }
    data.update(overrides)
    return data


@pytest.mark.django_db(transaction=True)
class TestBallotForms(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        TabSettings.set("cur_round", 2)

    def test_default_speak_precision_rejects_decimal_speaks(self):
        TabSettings.set("decimal_speaks", 0)
        round_obj = Round.objects.filter(round_number=1).first()

        form = ResultEntryForm(
            ballot_form_data(round_obj, pm_speaks="30.5"),
            round_instance=round_obj,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("Speaks must be whole numbers", form.errors["pm_speaks"])
        self.assertEqual(form.fields["pm_speaks"].widget.attrs["step"], "1")

    def test_half_point_speak_precision_accepts_halves_only(self):
        TabSettings.set("decimal_speaks", 1)
        round_obj = Round.objects.filter(round_number=1).first()

        valid_form = ResultEntryForm(
            ballot_form_data(
                round_obj,
                pm_speaks="30.5",
                mg_speaks="29.5",
                lo_speaks="28.5",
                mo_speaks="27.5",
            ),
            round_instance=round_obj,
        )
        invalid_form = ResultEntryForm(
            ballot_form_data(round_obj, pm_speaks="30.25"),
            round_instance=round_obj,
        )

        self.assertTrue(valid_form.is_valid(), valid_form.errors)
        self.assertFalse(invalid_form.is_valid())
        self.assertIn(
            "Speaks must be in half-point increments",
            invalid_form.errors["pm_speaks"],
        )
        self.assertEqual(valid_form.fields["pm_speaks"].widget.attrs["step"], "0.5")

    def test_speak_precision_accepts_string_setting_values(self):
        TabSettings.set("decimal_speaks", "2")
        round_obj = Round.objects.filter(round_number=1).first()

        form = ResultEntryForm(
            ballot_form_data(
                round_obj,
                pm_speaks="30.7",
                mg_speaks="29.3",
                lo_speaks="28.2",
                mo_speaks="27.1",
            ),
            round_instance=round_obj,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.fields["pm_speaks"].widget.attrs["step"], "0.1")

    def test_eballot_decimal_speak_precision_respects_setting(self):
        TabSettings.set("decimal_speaks", 2)
        round_obj = Round.objects.filter(round_number=1).first()
        judge = round_obj.chair or Judge.objects.first()
        judge.ballot_code = "DECIMAL1"
        judge.save()
        round_obj.chair = judge
        round_obj.save(update_fields=["chair"])
        RoundStats.objects.filter(round=round_obj).delete()

        valid_form = EBallotForm(
            ballot_form_data(
                round_obj,
                ballot_code=judge.ballot_code,
                pm_speaks="30.7",
                mg_speaks="29.3",
                lo_speaks="28.2",
                mo_speaks="27.1",
            ),
            round_instance=round_obj,
        )
        invalid_form = EBallotForm(
            ballot_form_data(
                round_obj,
                ballot_code=judge.ballot_code,
                pm_speaks="30.75",
            ),
            round_instance=round_obj,
        )

        self.assertTrue(valid_form.is_valid(), valid_form.errors)
        self.assertFalse(invalid_form.is_valid())
        self.assertIn(
            "Speaks may have at most 1 decimal digit",
            invalid_form.errors["pm_speaks"],
        )

        TabSettings.set("decimal_speaks", 3)
        hundredths_form = EBallotForm(
            ballot_form_data(
                round_obj,
                ballot_code=judge.ballot_code,
                pm_speaks="30.75",
            ),
            round_instance=round_obj,
        )

        self.assertTrue(hundredths_form.is_valid(), hundredths_form.errors)
        self.assertEqual(
            hundredths_form.fields["pm_speaks"].widget.attrs["step"],
            "0.01",
        )
