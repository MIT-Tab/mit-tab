from django.views import i18n
from django.conf.urls import url
from django.urls import path
from django.contrib import admin
from django.contrib.auth.views import LoginView

import mittab.apps.tab.views as views
import mittab.apps.tab.judge_views as judge_views
import mittab.apps.tab.team_views as team_views
import mittab.apps.tab.debater_views as debater_views
import mittab.apps.tab.pairing_views as pairing_views
import mittab.apps.tab.outround_pairing_views as outround_pairing_views


admin.autodiscover()

urlpatterns = [
    url(r"^admin/logout/$", views.tab_logout, name="admin_logout"),
    url(r"^accounts/logout/$", views.tab_logout, name="logout"),
    url(r"^admin/", admin.site.urls, name="admin"),
    url(r"^dynamic-media/jsi18n/$", i18n.JavaScriptCatalog.as_view(), name="js18"),
    url(r"^$", views.index, name="index"),
    url(r"^403/", views.render_403, name="403"),
    url(r"^404/", views.render_404, name="404"),
    url(r"^500/", views.render_500, name="500"),

    # Account related
    url(r"^accounts/login/$",
        LoginView.as_view(template_name="registration/login.html"),
        name="tab_login"),

    # Judge related
    url(r"^judges/", judge_views.public_view_judges, name="public_judges"),
    url(r"^judge/(\d+)/$", judge_views.view_judge, name="view_judge"),
    url(r"^judge/(\d+)/scratches/add/(\d+)/",
        judge_views.add_scratches,
        name="add_scratches"),
    url(r"^judge/(\d+)/scratches/view/",
        judge_views.view_scratches,
        name="view_scratches"),
    url(r"^judge/(\d+)/check_ins/round/(\d+)/$",
        judge_views.judge_check_in,
        name="judge_check_in"),
    url(r"^view_judges/$", judge_views.view_judges, name="view_judges"),
    url(r"^enter_judge/$", judge_views.enter_judge, name="enter_judge"),
    url(r"^batch_checkin/$", judge_views.batch_checkin, name="batch_checkin"),

    # School related
    url(r"^school/(\d+)/$", views.view_school, name="view_school"),
    url(r"^school/(\d+)/delete/$", views.delete_school, name="delete_school"),
    url(r"^view_schools/$", views.view_schools, name="view_schools"),
    url(r"^enter_school/$", views.enter_school, name="enter_school"),

    # Room related
    url(r"^room/(\d+)/$", views.view_room, name="view_room"),
    url(r"^view_rooms/$", views.view_rooms, name="view_rooms"),
    url(r"^enter_room/$", views.enter_room, name="enter_room"),

    # Scratch related
    url(r"^judge/(\d+)/scratches/delete/(\d+)/",
        views.delete_scratch,
        name="delete_scratch_judge"),
    url(r"^team/(\d+)/scratches/delete/(\d+)/",
        views.delete_scratch,
        name="delete_scratch_team"),
    url(r"^scratches/view/", views.view_scratches, name="view_scratches"),
    url(r"^enter_scratch/", views.add_scratch, name="add_scratch"),

    # Team related
    url(r"^teams/", team_views.public_view_teams, name="public_teams"),
    url(r"^team/(\d+)/$", team_views.view_team, name="view_team"),
    url(r"^team/(\d+)/scratches/add/(\d+)/",
        team_views.add_scratches,
        name="add_scratches"),
    url(r"^team/(\d+)/scratches/view/",
        team_views.view_scratches,
        name="view_scratches_team"),
    url(r"^team/(\d+)/stats/", team_views.team_stats, name="team_stats"),
    url(r"^view_teams/$", team_views.view_teams, name="view_teams"),
    url(r"^enter_team/$", team_views.enter_team, name="enter_team"),
    url(r"^all_tab_cards/$", team_views.all_tab_cards, name="all_tab_cards"),
    url(r"^team/card/(\d+)/$", team_views.tab_card, name="tab_card"),
    url(r"^team/card/(\d+)/pretty/$",
        team_views.pretty_tab_card,
        name="pretty_tab_card"),
    url(r"^team/ranking/$", team_views.rank_teams_ajax,
        name="rank_teams_ajax"),
    url(r"^team/rank/$", team_views.rank_teams, name="rank_teams"),

    # Debater related
    url(r"^debater/(\d+)/$", debater_views.view_debater, name="view_debater"),
    url(r"^view_debaters/$", debater_views.view_debaters,
        name="view_debaters"),
    url(r"^enter_debater/$", debater_views.enter_debater,
        name="enter_debater"),
    url(r"^debater/ranking/$",
        debater_views.rank_debaters_ajax,
        name="rank_debaters_ajax"),
    url(r"^debater/rank/$", debater_views.rank_debaters, name="rank_debaters"),

    # Pairing related
    url(r"^pairings/status/$", pairing_views.view_status, name="view_status"),
    url(r"^pairings/view_rounds/$",
        pairing_views.view_rounds,
        name="view_rounds"),
    url(r"^round/(\d+)/$", pairing_views.view_round, name="view_round"),
    url(r"^round/(\d+)/result/$",
        pairing_views.enter_result,
        name="enter_result"),
    url(r"^round/(\d+)/result/(\d+)/$",
        pairing_views.enter_multiple_results,
        name="enter_multiple_results"),
    url(r"^round/(\d+)/alternative_judges/(\d+)/$",
        pairing_views.alternative_judges,
        name="round_alternative_judges"),
    url(r"^round/(\d+)/(\d+)/alternative_teams/(gov|opp)/$",
        pairing_views.alternative_teams,
        name="round_alternative_teams"),
    url(r"^round/(\d+)/alternative_judges/$",
        pairing_views.alternative_judges,
        name="alternative_judges"),
    url(r"^round/(\d+)/assign_judge/(\d+)/$",
        pairing_views.assign_judge,
        name="assign_judge"),
    url(r"^pairings/assign_team/(\d+)/(gov|opp)/(\d+)/$",
        pairing_views.assign_team,
        name="assign_team"),
    url(r"^round/(\d+)/assign_judge/(\d+)/(\d+)/$",
        pairing_views.assign_judge,
        name="swap_judge"),
    url(r"^pairing/pair_round/$", pairing_views.pair_round, name="pair_round"),
    url(r"^pairing/assign_judges/$",
        pairing_views.assign_judges_to_pairing,
        name="assign_judges"),
    url(r"^pairing/confirm_start_tourny/$",
        pairing_views.confirm_start_new_tourny,
        name="confirm_start_tourny"),
    url(r"^pairing/start_tourny/$",
        pairing_views.start_new_tourny,
        name="start_tourny"),
    url(r"^pairings/pairinglist/$",
        pairing_views.pretty_pair,
        name="pretty_pair"),
    url(r"^pairings/missing_ballots/$",
        pairing_views.missing_ballots,
        name="missing_ballots"),
    url(r"^pairings/pairinglist/printable/$",
        pairing_views.pretty_pair_print,
        name="pretty_pair_print"),
    url(r"^pairing/backup/$",
        pairing_views.manual_backup,
        name="manual_backup"),
    url(r"^pairing/release/$",
        pairing_views.toggle_pairing_released,
        name="toggle_pairing_released"),
    url(r"^pairing/view_backups/$",
        pairing_views.view_backups,
        name="view_backups"),
    url(r"^e_ballots/$", pairing_views.e_ballot_search,
        name="e_ballot_search"),
    url(r"e_ballots/(\S+)/$",
        pairing_views.enter_e_ballot,
        name="enter_e_ballot"),

    # Outround related
    url(r"break/",
        outround_pairing_views.break_teams,
        name="break"),
    path("outround_pairing/<int:type_of_round>/<int:num_teams>",
         outround_pairing_views.outround_pairing_view,
         name="outround_pairing_view"),
    path("outround_pairing",
         outround_pairing_views.outround_pairing_view,
         name="outround_pairing_view_default"),
    url(r"^outround/(\d+)/alternative_judges/(\d+)/$",
        outround_pairing_views.alternative_judges,
        name="outround_alternative_judges"),
    url(r"^outround/(\d+)/(\d+)/alternative_teams/(gov|opp)/$",
        outround_pairing_views.alternative_teams,
        name="outround_alternative_teams"),
    url(r"^outround/(\d+)/alternative_judges/$",
        outround_pairing_views.alternative_judges,
        name="outround_alternative_judges"),
    url(r"^outround/(\d+)/assign_judge/(\d+)/$",
        outround_pairing_views.assign_judge,
        name="outround_assign_judge"),
    url(r"^outround/pairings/assign_team/(\d+)/(gov|opp)/(\d+)/$",
        outround_pairing_views.assign_team,
        name="outround_assign_team"),
    url(r"^outround/(\d+)/assign_judge/(\d+)/(\d+)/$",
        outround_pairing_views.assign_judge,
        name="outround_swap_judge"),

    # Settings related
    url(r"^settings_form",
        views.settings_form,
        name="settings_form"),

    # Backups
    url(r"^backup/restore/(.+)/$",
        pairing_views.restore_backup,
        name="restore_backup"),
    url(r"^backup/download/(.+)/$",
        pairing_views.download_backup,
        name="download_backup"),
    url(r"^backup/(.+)/$", pairing_views.view_backup, name="view_backup"),
    url(r"^upload_backup/$", pairing_views.upload_backup,
        name="upload_backup"),

    # Data Upload
    url(r"^import_data/$", views.upload_data, name="upload_data"),

    # Tournament Archive
    url(r"^archive/download/$", views.generate_archive, name="download_archive"),

    # Cache related
    url(r"^cache_refresh", views.force_cache_refresh, name="cache_refresh")
]

handler403 = "mittab.apps.tab.views.render_403"
handler404 = "mittab.apps.tab.views.render_404"
handler500 = "mittab.apps.tab.views.render_500"
