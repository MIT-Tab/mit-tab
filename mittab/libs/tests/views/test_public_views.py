from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import caches
from django.http import HttpResponse
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from nplusone.core import profiler

from mittab.apps.tab.models import (
    Judge,
    Outround,
    PublicHomePage,
    PublicHomeShortcut,
    Room,
    Round,
    RoundStats,
    SPEAKER_SINGLE_ADJUSTED_RANKINGS_SETTING,
    TabSettings,
    Team,
)
from mittab.apps.tab.public_rankings import (
    get_ballot_round_settings,
    get_public_display_flags,
    get_ranking_settings,
    get_standings_publication_setting,
    set_ballot_round_settings,
    set_ranking_settings,
)
from mittab.apps.tab.views.public_views import (
    _build_disclosure_message,
    _submitted_ballot_context,
)
from mittab.libs.cacheing import cache_logic


@pytest.mark.django_db(transaction=True)
class TestPublicViews(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        caches["public"].clear()
        cache_logic.clear_cache()

        # Ensure ballots are eligible for public display regardless of fixture defaults
        Team.objects.update(ranking_public=True)

        Outround(
            gov_team=Team.objects.first(),
            opp_team=Team.objects.last(),
            num_teams=2,
            type_of_round=Outround.NOVICE,
            room=Room.objects.first(),
        ).save()
        Outround(
            gov_team=Team.objects.first(),
            opp_team=Team.objects.last(),
            num_teams=2,
            type_of_round=Outround.VARSITY,
            room=Room.objects.last(),
        ).save()

        # Pairing for the next round (3) has started, so round 2 ballots may show
        TabSettings.set("cur_round", 3)
        TabSettings.set("tot_rounds", 5)

        # Mark the most recent round (cur_round - 1) as missing a ballot
        self.test_round = Round.objects.filter(
            round_number=TabSettings.get("cur_round") - 1
        ).first()
        self.original_victor = self.test_round.victor
        self.test_round.victor = Round.NONE
        self.test_round.save()
        TabSettings.set("pairing_released", 1)
        TabSettings.set("judges_public", 1)
        TabSettings.set("teams_public", 1)
        TabSettings.set("debaters_public", 1)
        TabSettings.set("var_teams_visible", 2)  # Show finals and above
        TabSettings.set("nov_teams_visible", 2)  # Show finals and above

        set_ranking_settings("team", True, include_speaks=True, max_visible=1000)
        set_ranking_settings("varsity", True, include_speaks=True, max_visible=10)
        set_ranking_settings("novice", True, include_speaks=True, max_visible=10)
        set_ballot_round_settings(1, True, include_speaks=False, include_ranks=False)

    def tearDown(self):
        # Restore the original victor value to avoid polluting other tests
        self.test_round.victor = self.original_victor
        self.test_round.save()
        caches["public"].clear()
        cache_logic.clear_cache()
        super().tearDown()

    def get_test_objects(self):
        judge = self.test_round.chair
        team = Team.objects.first()
        round_obj = Round.objects.filter(round_number=1, gov_team__isnull=False).first()
        v_out = Outround.objects.filter(type_of_round=0).first()
        n_out = Outround.objects.filter(type_of_round=1).first()
        gov_debater = round_obj.gov_team.debaters.first()

        # Ensure judge has a ballot code for e-ballot tests
        judge.ballot_code = "TEST123"
        judge.save()

        return judge, team, round_obj, v_out, n_out, gov_debater

    def _set_disclosure_settings(self, open_speaks, open_ranks):
        """Set disclosure settings.
        
        Values: None=unset, True=open (1), False=closed (2)
        """
        for key, value in (("open_speaks", open_speaks), ("open_ranks", open_ranks)):
            if value is None:
                # Set to 0 (unset)
                TabSettings.set(key, 0)
            else:
                # Convert True -> 1 (open), False -> 2 (closed)
                TabSettings.set(key, 1 if value else 2)

        caches["public"].clear()
        cache_logic.clear_cache()

    def _create_round_stats(self, round_obj):
        RoundStats.objects.filter(round=round_obj).delete()

        gov_debaters = list(round_obj.gov_team.debaters.all()[:2])
        opp_debaters = list(round_obj.opp_team.debaters.all()[:2])
        roles = [
            (gov_debaters[0], "pm", 28, 1),
            (gov_debaters[1], "mg", 27, 2),
            (opp_debaters[0], "lo", 26, 3),
            (opp_debaters[1], "mo", 25, 4),
        ]

        for debater, role, speaks, ranks in roles:
            RoundStats.objects.create(
                debater=debater,
                round=round_obj,
                speaks=speaks,
                ranks=ranks,
                debater_role=role,
            )

        round_obj.victor = Round.GOV
        round_obj.save()

    def _prepare_ballot_round(self):
        judge = self.test_round.chair
        judge.ballot_code = "TEST123"
        judge.save()
        self.test_round.chair = judge
        self.test_round.save()
        self.test_round.judges.add(judge)
        return judge, self.test_round

    def _make_judge(self, name):
        return Judge.objects.create(name=name, rank=Decimal("4.50"))

    def _make_additional_round(self, judge, chair=None):
        teams = list(Team.objects.exclude(
            id__in=[self.test_round.gov_team_id, self.test_round.opp_team_id]
        )[:2])
        extra_round = Round.objects.create(
            round_number=self.test_round.round_number,
            gov_team=teams[0],
            opp_team=teams[1],
            chair=chair or judge,
            room=Room.objects.last(),
        )
        extra_round.judges.add(judge)
        if chair and chair != judge:
            extra_round.judges.add(chair)
        return extra_round

    def test_render(self):
        client = Client()

        (judge, team, round_obj, v_out,
         n_out, gov_debater) = self.get_test_objects()

        view_content_tests = [
            (reverse("public_judges"), [judge.name]),
            (reverse("public_teams"), [team.name]),
            (reverse("rank_teams_public"), [team.name]),
            (reverse("pretty_pair"), [round_obj.gov_team.name, gov_debater.name]),
            (reverse("missing_ballots"), [self.test_round.chair.name]),
            (reverse("e_ballot_search"), ["Submit E-Ballot"]),
            (reverse("outround_pretty_pair", args=[0]), [v_out.gov_team.name]),
            (reverse("outround_pretty_pair", args=[1]), [n_out.gov_team.name]),
            (reverse("public_home"), ["Released Pairings"]),
        ]

        for url, expected_content in view_content_tests:
            response = client.get(url)
            self.assertEqual(response.status_code, 200,
                f"Failed to render {url}, got status {response.status_code}")

            for content in expected_content:
                self.assertIn(content, response.content.decode(),
                    f"Expected content '{content}' not found in {url}")

        if judge and judge.ballot_code:
            chair_round = Round.objects.filter(round_number=1).first()
            if chair_round:
                chair_round.chair = judge
                chair_round.save()

                url = reverse("enter_e_ballot", args=[judge.ballot_code])
                response = client.get(url)
                self.assertIn(response.status_code, [200, 302],
                    "Failed to handle e-ballot entry for valid ballot code")

    def test_disclosure_message_allows_missing_settings(self):
        # Set both to 0 (unset)
        TabSettings.set("open_speaks", 0)
        TabSettings.set("open_ranks", 0)

        caches["public"].clear()
        cache_logic.clear_cache()

        self.assertIsNone(_build_disclosure_message())

    def test_disclosure_message_variants(self):
        scenarios = [
            (True, True, "open speaks and open ranks"),
            (True, False, "open speaks but closed ranks"),
            (False, True, "closed speaks but open ranks"),
            (False, False, "closed speaks and closed ranks"),
            (True, None, "This tournament is open speaks."),
            (False, None, "This tournament is closed speaks."),
            (None, True, "This tournament is open ranks."),
            (None, False, "This tournament is closed ranks."),
        ]

        for open_speaks, open_ranks, expected in scenarios:
            with self.subTest(open_speaks=open_speaks, open_ranks=open_ranks):
                self._set_disclosure_settings(open_speaks, open_ranks)
                self.assertIn(expected, _build_disclosure_message())

    def test_tabsettings_get_returns_explicit_default_for_missing_key(self):
        for setting in TabSettings.objects.filter(key="missing_disclosure_setting"):
            setting.delete()

        caches["public"].clear()
        cache_logic.clear_cache()

        self.assertIsNone(TabSettings.get("missing_disclosure_setting", None))
        self.assertEqual(
            TabSettings.get("missing_disclosure_setting", 0),
            0,
        )

    def test_tabsettings_get_raises_without_default_for_missing_key(self):
        for setting in TabSettings.objects.filter(key="missing_disclosure_setting"):
            setting.delete()

        caches["public"].clear()
        cache_logic.clear_cache()

        with self.assertRaisesRegex(
            ValueError,
            "No TabSetting with key 'missing_disclosure_setting'",
        ):
            TabSettings.get("missing_disclosure_setting")

    def test_submitted_ballot_context_includes_placeholders_for_missing_stats(self):
        round_obj = Round.objects.filter(round_number=1, gov_team__isnull=False).first()
        RoundStats.objects.filter(round=round_obj).delete()
        round_obj.victor = Round.UNKNOWN
        round_obj.save()

        RoundStats.objects.create(
            debater=round_obj.gov_team.debaters.first(),
            round=round_obj,
            speaks=29,
            ranks=1,
            debater_role="pm",
        )
        RoundStats.objects.create(
            debater=round_obj.opp_team.debaters.first(),
            round=round_obj,
            speaks=28,
            ranks=2,
            debater_role="lo",
        )

        context = _submitted_ballot_context(round_obj, "CTX123")

        self.assertEqual(context["ballot_code"], "CTX123")
        self.assertEqual(context["winner_display"], "UNKNOWN")
        self.assertEqual(
            context["gov_debaters"][0]["name"],
            round_obj.gov_team.debaters.first().name,
        )
        self.assertEqual(context["gov_debaters"][1]["name"], "—")
        self.assertEqual(
            context["opp_debaters"][0]["name"],
            round_obj.opp_team.debaters.first().name,
        )
        self.assertEqual(context["opp_debaters"][1]["name"], "—")

    def test_view_submitted_ballot_redirects_without_matching_judge(self):
        client = Client()

        response = client.get(reverse("view_submitted_ballot", args=["BADCODE"]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("tab_login"))

    def test_view_submitted_ballot_redirects_when_ballot_not_submitted(self):
        client = Client()
        judge, round_obj = self._prepare_ballot_round()
        RoundStats.objects.filter(round=round_obj).delete()

        response = client.get(
            reverse("view_submitted_ballot", args=[judge.ballot_code])
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("e_ballot_search"))

    def test_view_submitted_ballot_renders_submitted_ballot(self):
        client = Client()
        judge, round_obj = self._prepare_ballot_round()
        self._set_disclosure_settings(True, False)
        self._create_round_stats(round_obj)

        response = client.get(
            reverse("view_submitted_ballot", args=[judge.ballot_code])
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Ballot submitted", content)
        self.assertIn(round_obj.gov_team.display, content)
        self.assertIn(round_obj.opp_team.display, content)
        self.assertIn("Speaker Disclosure Policy", content)
        self.assertIn("open speaks but closed ranks", content)

    def test_submitted_ballot_allows_written_rfd_until_deadline(self):
        client = Client()
        judge, round_obj = self._prepare_ballot_round()
        self._create_round_stats(round_obj)
        TabSettings.set("written_rfd_first_round", round_obj.round_number)
        TabSettings.set("written_rfd_deadline", "2099-01-01 12:00")

        response = client.get(
            reverse("view_submitted_ballot", args=[judge.ballot_code])
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Reason for Decision", content)
        self.assertIn("Save Written RFD", content)
        self.assertIn("You can edit this written RFD until", content)

        response = client.post(
            reverse("view_submitted_ballot", args=[judge.ballot_code]),
            {"rfd": "Gov won the central weighing clash."},
        )
        self.assertEqual(response.status_code, 302)
        round_obj.refresh_from_db()
        self.assertEqual(round_obj.rfd, "Gov won the central weighing clash.")

    def test_submitted_ballot_blocks_written_rfd_after_deadline(self):
        client = Client()
        judge, round_obj = self._prepare_ballot_round()
        self._create_round_stats(round_obj)
        round_obj.rfd = "Original RFD"
        round_obj.save(update_fields=["rfd"])
        TabSettings.set("written_rfd_first_round", round_obj.round_number)
        TabSettings.set("written_rfd_deadline", "2000-01-01 12:00")

        response = client.post(
            reverse("view_submitted_ballot", args=[judge.ballot_code]),
            {"rfd": "Changed too late"},
        )
        self.assertEqual(response.status_code, 302)
        round_obj.refresh_from_db()
        self.assertEqual(round_obj.rfd, "Original RFD")

    def test_previous_ballots_lists_submitted_prior_rounds(self):
        client = Client()
        judge, _ = self._prepare_ballot_round()
        previous_round = Round.objects.filter(round_number=1).first()
        previous_round.chair = judge
        previous_round.save()
        previous_round.judges.add(judge)
        self._create_round_stats(previous_round)
        TabSettings.set("written_rfd_first_round", previous_round.round_number)

        response = client.get(reverse("previous_ballots", args=[judge.ballot_code]))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Previous Ballots", content)
        self.assertIn(f"Round {previous_round.round_number}", content)
        self.assertIn(
            reverse(
                "view_submitted_ballot_round",
                args=[judge.ballot_code, previous_round.id],
            ),
            content,
        )

    def test_previous_ballots_lists_submitted_current_ballot_round(self):
        client = Client()
        judge, round_obj = self._prepare_ballot_round()
        self._create_round_stats(round_obj)

        response = client.get(reverse("previous_ballots", args=[judge.ballot_code]))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(f"Round {round_obj.round_number}", content)
        self.assertIn(
            reverse(
                "view_submitted_ballot_round",
                args=[judge.ballot_code, round_obj.id],
            ),
            content,
        )

    def test_enter_e_ballot_post_missing_round_id_redirects(self):
        client = Client()
        judge, _ = self._prepare_ballot_round()

        response = client.post(reverse("enter_e_ballot", args=[judge.ballot_code]), {})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("e_ballot_search"))

    def test_enter_e_ballot_post_invalid_form_rerenders_entry(self):
        client = Client()
        judge, round_obj = self._prepare_ballot_round()

        response = client.post(
            reverse("enter_e_ballot", args=[judge.ballot_code]),
            {"round_instance": round_obj.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Entering Ballot for", response.content.decode())

    def test_enter_e_ballot_post_valid_redirects_to_submitted_view(self):
        client = Client()
        judge, round_obj = self._prepare_ballot_round()

        with patch("mittab.apps.tab.views.public_views.EBallotForm") as mock_form_class:
            mock_form = MagicMock()
            mock_form.is_valid.return_value = True
            mock_form.save.return_value = round_obj
            mock_form_class.return_value = mock_form

            response = client.post(
                reverse("enter_e_ballot", args=[judge.ballot_code]),
                {"round_instance": round_obj.id},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("view_submitted_ballot", kwargs={"ballot_code": judge.ballot_code}),
        )

    def test_enter_e_ballot_post_save_error_redirects(self):
        client = Client()
        judge, round_obj = self._prepare_ballot_round()

        with patch("mittab.apps.tab.views.public_views.EBallotForm") as mock_form_class:
            mock_form = MagicMock()
            mock_form.is_valid.return_value = True
            mock_form.save.side_effect = ValueError("bad ballot")
            mock_form_class.return_value = mock_form

            response = client.post(
                reverse("enter_e_ballot", args=[judge.ballot_code]),
                {"round_instance": round_obj.id},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("e_ballot_search"))

    def test_enter_e_ballot_get_renders_submitted_ballot_when_already_entered(self):
        client = Client()
        judge, round_obj = self._prepare_ballot_round()
        self._create_round_stats(round_obj)

        response = client.get(reverse("enter_e_ballot", args=[judge.ballot_code]))

        self.assertEqual(response.status_code, 200)
        self.assertIn("Ballot submitted", response.content.decode())

    def test_enter_e_ballot_get_redirects_when_judge_code_missing(self):
        client = Client()

        response = client.get(reverse("enter_e_ballot", args=["BADCODE"]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("tab_login"))

    def test_enter_e_ballot_redirects_to_previous_ballots_when_pairings_not_released(
        self,
    ):
        client = Client()
        judge, _ = self._prepare_ballot_round()
        TabSettings.set("pairing_released", 0)

        response = client.get(reverse("enter_e_ballot", args=[judge.ballot_code]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("previous_ballots", args=[judge.ballot_code]),
        )

    def test_enter_e_ballot_get_redirects_when_no_round_found(self):
        client = Client()
        judge = self._make_judge("No Round Judge")
        judge.ballot_code = "NOROUND"
        judge.save()

        response = client.get(reverse("enter_e_ballot", args=[judge.ballot_code]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("tab_login"))

    def test_enter_e_ballot_get_redirects_when_not_chair(self):
        client = Client()
        judge, round_obj = self._prepare_ballot_round()
        actual_chair = self._make_judge("Actual Chair Judge")
        round_obj.chair = actual_chair
        round_obj.save()
        round_obj.judges.add(actual_chair)

        response = client.get(reverse("enter_e_ballot", args=[judge.ballot_code]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("tab_login"))

    def test_enter_e_ballot_get_redirects_when_multiple_rounds_found(self):
        client = Client()
        judge, _ = self._prepare_ballot_round()
        self._make_additional_round(judge)

        response = client.get(reverse("enter_e_ballot", args=[judge.ballot_code]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("tab_login"))

    def test_enter_e_ballot_get_delegates_to_result_entry_when_ballot_missing(self):
        client = Client()
        judge, round_obj = self._prepare_ballot_round()
        RoundStats.objects.filter(round=round_obj).delete()

        with patch(
            "mittab.apps.tab.views.public_views.enter_result"
        ) as mock_enter_result:
            mock_enter_result.return_value = HttpResponse("entry-form")

            response = client.get(reverse("enter_e_ballot", args=[judge.ballot_code]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "entry-form")
        self.assertEqual(mock_enter_result.call_count, 1)
        self.assertEqual(mock_enter_result.call_args.args[1], round_obj.id)
        self.assertEqual(mock_enter_result.call_args.args[3], judge.ballot_code)

    def test_permissions(self):
        client = Client()

        (judge, team, _, v_out,
         n_out, gov_debater) = self.get_test_objects()

        scenarios = [
            {
                "url": reverse("public_judges"),
                "allowed": lambda: TabSettings.set("judges_public", 1),
                "denied": lambda: TabSettings.set("judges_public", 0),
                "expected": judge.name,
                "status_allowed": 200,
                "status_denied": 302,
            },
            {
                "url": reverse("public_teams"),
                "allowed": lambda: TabSettings.set("teams_public", 1),
                "denied": lambda: TabSettings.set("teams_public", 0),
                "expected": team.name,
                "status_allowed": 200,
                "status_denied": 302,
            },
            {
                "url": reverse("rank_teams_public"),
                "allowed": lambda: set_ranking_settings("team", True, True, 1000),
                "denied": lambda: set_ranking_settings("team", False, True, 1000),
                "expected": team.name,
                "status_allowed": 200,
                "status_denied": 302,
            },
            {
                "url": reverse("pretty_pair"),
                "allowed": lambda: TabSettings.set("pairing_released", 1),
                "denied": lambda: TabSettings.set("pairing_released", 0),
                "expected": gov_debater.name,
                "status_allowed": 200,
                "status_denied": 200,
            },
            {
                "url": reverse("missing_ballots"),
                "allowed": lambda: TabSettings.set("pairing_released", 1),
                "denied": lambda: TabSettings.set("pairing_released", 0),
                "expected": self.test_round.gov_team.display_backend,
                "status_allowed": 200,
                "status_denied": 200,
            },
            {
                "url": reverse("outround_pretty_pair", args=[0]),
                "allowed": lambda: TabSettings.set("var_teams_visible", 2),
                "denied": lambda: TabSettings.set("var_teams_visible", 16),
                "expected": v_out.gov_team.name,
                "status_allowed": 200,
                "status_denied": 200,
            },
            {
                "url": reverse("outround_pretty_pair", args=[1]),
                "allowed": lambda: TabSettings.set("nov_teams_visible", 2),
                "denied": lambda: TabSettings.set("nov_teams_visible", 16),
                "expected": n_out.gov_team.name,
                "status_allowed": 200,
                "status_denied": 200,
            },
        ]

        for scenario in scenarios:
            scenario["allowed"]()
            caches["public"].clear()
            response = client.get(scenario["url"])
            self.assertEqual(
                response.status_code,
                scenario["status_allowed"],
                f"Expected {scenario['status_allowed']} for "
                f" {scenario['url']} when allowed",
            )
            self.assertIn(
                scenario["expected"],
                response.content.decode(),
                f"Expected '{scenario['expected']}' to be "
                f" visible for {scenario['url']}",
            )

            scenario["denied"]()
            caches["public"].clear()
            response = client.get(scenario["url"])
            self.assertEqual(
                response.status_code,
                scenario["status_denied"],
                f"Expected {scenario['status_denied']} for "
                f" {scenario['url']} when denied",
            )
            self.assertNotIn(
                scenario["expected"],
                response.content.decode(),
                f"Expected '{scenario['expected']}' to be hidden for {scenario['url']}",
            )

    def test_public_speaker_rankings_respect_settings(self):
        client = Client()
        response = client.get(reverse("public_speaker_rankings"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Speaker Rankings", content)
        self.assertIn("Varsity Speakers", content)
        self.assertIn("Novice Speakers", content)

        set_ranking_settings("varsity", False, True, 5)
        set_ranking_settings("novice", False, True, 5)
        caches["public"].clear()
        response = client.get(reverse("public_speaker_rankings"))
        self.assertEqual(response.status_code, 302,
            "Speaker rankings should redirect when no divisions are public")

        set_ranking_settings("varsity", True, False, 1)
        caches["public"].clear()
        response = client.get(reverse("public_speaker_rankings"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Varsity Speakers", content)
        self.assertIn("Speaks and ranks are hidden for this section.", content)
        self.assertNotIn("Novice Speakers", content,
            "Hidden divisions should not render a section")

    def test_public_speaker_rankings_orders_display_score_columns_by_setting(self):
        client = Client()
        TabSettings.set(SPEAKER_SINGLE_ADJUSTED_RANKINGS_SETTING, 0)
        caches["public"].clear()
        response = client.get(reverse("public_speaker_rankings"))
        content = response.content.decode()
        self.assertNotIn("Score Type", content)
        self.assertLess(
            content.index("Unadjusted"),
            content.index("Single adjusted"),
        )

        TabSettings.set(SPEAKER_SINGLE_ADJUSTED_RANKINGS_SETTING, 1)
        caches["public"].clear()
        response = client.get(reverse("public_speaker_rankings"))
        content = response.content.decode()
        self.assertNotIn("Score Type", content)
        self.assertLess(
            content.index("Single adjusted"),
            content.index("Unadjusted"),
        )

    def test_public_home_uses_shortcut_configuration(self):
        client = Client()

        PublicHomeShortcut.objects.filter(position=2).update(
            nav_item="public_team_results"
        )
        caches["public"].clear()

        response = client.get(reverse("public_home"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn("Public Team Results", content)
        self.assertIn(
            f'class="tile shadow-sm" href="{reverse("rank_teams_public")}"',
            content,
        )
        self.assertIn(
            f'class="tile shadow-sm" href="{reverse("pretty_pair")}"',
            content,
        )
        self.assertNotIn(
            f'class="tile shadow-sm" href="{reverse("missing_ballots")}"',
            content,
        )

    def test_public_home_falls_back_to_defaults_when_shortcuts_missing(self):
        client = Client()

        PublicHomeShortcut.objects.all().delete()
        caches["public"].clear()

        response = client.get(reverse("public_home"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(reverse("pretty_pair"), content)
        self.assertIn(reverse("missing_ballots"), content)

    def test_public_home_hides_inactive_configured_shortcuts(self):
        client = Client()

        PublicHomePage.ensure_defaults()
        PublicHomePage.objects.filter(slug="public_team_results").update(
            is_active=False
        )
        PublicHomeShortcut.objects.update_or_create(
            position=2,
            defaults={"nav_item": "public_team_results"},
        )
        caches["public"].clear()

        response = client.get(reverse("public_home"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertNotIn(
            f'class="tile shadow-sm" href="{reverse("rank_teams_public")}"',
            content,
        )
        self.assertIn(
            f'class="tile shadow-sm" href="{reverse("missing_ballots")}"',
            content,
        )

    def test_public_home_shows_only_eight_shortcuts_when_motions_enabled(self):
        client = Client()
        TabSettings.set("motions_enabled", 1)

        defaults = PublicHomeShortcut.default_slot_mapping()
        for position, nav_item in defaults.items():
            PublicHomeShortcut.objects.update_or_create(
                position=position,
                defaults={"nav_item": nav_item},
            )

        caches["public"].clear()
        response = client.get(reverse("public_home"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertEqual(content.count('class="tile shadow-sm"'), 8)
        self.assertNotIn('<span class="title">Motions</span>', content)

    def test_public_home_can_show_motions_when_selected_as_shortcut(self):
        client = Client()
        TabSettings.set("motions_enabled", 1)

        defaults = PublicHomeShortcut.default_slot_mapping()
        defaults[2] = "public_motions"
        for position, nav_item in defaults.items():
            PublicHomeShortcut.objects.update_or_create(
                position=position,
                defaults={"nav_item": nav_item},
            )

        caches["public"].clear()
        response = client.get(reverse("public_home"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertEqual(content.count('class="tile shadow-sm"'), 8)
        self.assertIn('<span class="title">Motions</span>', content)
        self.assertIn(reverse("public_motions"), content)

    def test_public_rankings_control_updates_display_settings(self):
        client = Client()
        user = get_user_model().objects.create_superuser(
            username="director",
            email="director@example.com",
            password="password",
        )
        client.force_login(user)
        TabSettings.set("tot_rounds", 2)
        set_ranking_settings("team", False, False, 3)
        set_ranking_settings("varsity", False, True, 3)
        set_ranking_settings("novice", False, True, 3)
        set_ballot_round_settings(1, False, False, False)
        set_ballot_round_settings(2, False, False, False)

        form_data = {
            "standings_team_results": "on",
            "standings_speaker_results": "on",
            "team_public": "on",
            "team_include_speaks": "on",
            "team_max_visible": "25",
            "varsity_max_visible": "5",
            "novice_max_visible": "7",
            "round_1_visible": "on",
            "round_1_include_speaks": "on",
            "round_1_include_ranks": "",
            "round_2_include_speaks": "",
            "round_2_include_ranks": "",
        }
        response = client.post(
            reverse("public_rankings_control"),
            form_data,
        )
        self.assertEqual(response.status_code, 302)

        team_settings = get_ranking_settings("team")
        self.assertTrue(team_settings["public"])
        self.assertTrue(team_settings["include_speaks"])
        self.assertEqual(team_settings["max_visible"], 25)

        speaker_published = get_standings_publication_setting("speaker_results")
        team_published = get_standings_publication_setting("team_results")
        self.assertTrue(speaker_published["published"])
        self.assertTrue(team_published["published"])

        round1_settings = get_ballot_round_settings(1)
        self.assertTrue(round1_settings["visible"])
        self.assertTrue(round1_settings["include_speaks"])
        self.assertFalse(round1_settings["include_ranks"])

        round2_settings = get_ballot_round_settings(2)
        self.assertFalse(round2_settings["visible"],
            "Rounds omitted from the form should remain hidden")

    def test_public_ballot_modes(self):
        client = Client()

        set_ballot_round_settings(1, True, include_speaks=False, include_ranks=False)
        caches["public"].clear()
        response = client.get(reverse("public_ballots"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Winner:", content)
        self.assertIn("Speaks and ranks are hidden", content)

        set_ballot_round_settings(1, True, include_speaks=True, include_ranks=True)
        caches["public"].clear()
        response = client.get(reverse("public_ballots"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn("Speaks and ranks are hidden", content)

    def test_team_rankings_without_speaks(self):
        client = Client()

        set_ranking_settings("team", True, include_speaks=False, max_visible=1000)
        caches["public"].clear()

        response = client.get(reverse("rank_teams_public"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Speaks and ranks are hidden", content)
        self.assertNotIn("<th>Speaks</th>", content)

    def test_public_ballots_hidden_until_next_round(self):
        client = Client()

        set_ballot_round_settings(1, False, False, False)
        caches["public"].clear()
        response = client.get(reverse("public_ballots"))
        self.assertEqual(response.status_code, 302)

    def test_manual_release_allows_current_round_ballots(self):
        client = Client()

        set_ballot_round_settings(1, False, False, False)
        caches["public"].clear()
        response = client.get(reverse("public_ballots"))
        self.assertEqual(response.status_code, 302)

        set_ballot_round_settings(1, True, True, True)
        caches["public"].clear()
        response = client.get(reverse("public_ballots"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Winner:", response.content.decode())

    def test_public_display_flags_follow_setting_changes(self):
        set_ranking_settings("team", False, True, 1000)
        flags = get_public_display_flags()
        self.assertFalse(flags["team_results"])

        set_ranking_settings("team", True, True, 1000)
        flags = get_public_display_flags()
        self.assertTrue(flags["team_results"])

        set_ballot_round_settings(1, False, False, False)
        flags = get_public_display_flags()
        self.assertFalse(flags["ballots"])

        set_ballot_round_settings(1, True, False, False)
        flags = get_public_display_flags()
        self.assertTrue(flags["ballots"])


    def test_n_plus_one(self):
        client = Client()

        views_to_test = [
            (("public_judges",), None),
            (("public_teams",), None),
            (("rank_teams_public",), None),
            (("pretty_pair",), None),
            (("missing_ballots",), None),
            (("outround_pretty_pair",), [0]),
            (("outround_pretty_pair",), [1]),
            (("public_home",), None),
        ]

        for view_name, url_args in views_to_test:
            with profiler.Profiler():
                if url_args:
                    response = client.get(reverse(*view_name, args=url_args))
                else:
                    response = client.get(reverse(*view_name))
                self.assertEqual(response.status_code, 200)
