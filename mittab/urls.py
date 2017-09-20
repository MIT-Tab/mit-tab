from django.conf.urls import include, url
from django.contrib.auth.views import logout
from django.views.i18n import javascript_catalog
from django.conf import settings
import apps.tab.views as views
import apps.tab.judge_views as judge_views
import apps.tab.team_views as team_views
import apps.tab.debater_views as debater_views
import apps.tab.pairing_views as pairing_views

from django.contrib import admin
admin.autodiscover()

urlpatterns = [
    # Example:
    # url(r'^tab/', include('tab.foo.urls')),

    # Uncomment the admin/doc line below to enable admin documentation:
    # url(r'^admin/doc/', include('django.contrib.admindocs.urls')),

    url(r'^admin/', include(admin.site.urls)),
    url(r'^dynamic-media/jsi18n/$', javascript_catalog),

    url(r'^$', views.index, name='index'),
    url(r'^403/', views.render_403),

    # Account related
    url(r'^accounts/login/$',  views.tab_login, name='login'),
    url(r'^accounts/logout/$', logout, name='logout'),

    # Judge related
    url(r'^judge/(\d+)/$', judge_views.view_judge, name='judge'),
    url(r'^judge/(\d+)/delete/$', judge_views.delete_judge, name='delete-judge'),
    url(r'^judge/(\d+)/scratches/add/(\d+)/', judge_views.add_scratches, name='new-judge-scratch'),
    url(r'^judge/(\d+)/scratches/view/', judge_views.view_scratches, name='judge-scratches'),
    url(r'^view_judges/$', judge_views.view_judges, name='judges'),
    url(r'^enter_judge/$', judge_views.enter_judge, name='new-judge'),

    # School related
    url(r'^school/(\d+)/$', views.view_school, name='school'),
    url(r'^school/(\d+)/delete/$', views.delete_school, name='delete-school'),
    url(r'^view_schools/$', views.view_schools, name='schools'),
    url(r'^enter_school/$', views.enter_school, name='new-school'),

    # Room related
    url(r'^room/(\d+)/$', views.view_room, name='room'),
    url(r'^room/(\d+)/delete/$', views.delete_room, name='delete-room'),
    url(r'^view_rooms/$', views.view_rooms, name='rooms'),
    url(r'^enter_room/$', views.enter_room, name='new-room'),

    # Scratch related
    url(r'^judge/(\d+)/scratches/delete/(\d+)/', views.delete_scratch,
        name='delete-judge-scratch'),
    url(r'^team/(\d+)/scratches/delete/(\d+)/', views.delete_scratch,
        name='delete-team-scratch'),
    url(r'^scratches/view/', views.view_scratches, name='scratches'),
    url(r'^enter_scratch/', views.add_scratch, name='new-scratch'),

    # Team related
    url(r'^team/(\d+)/$', team_views.view_team, name='team'),
    url(r'^team/(\d+)/delete/$', team_views.delete_team, name='delete-team'),
    url(r'^team/(\d+)/scratches/add/(\d+)/', team_views.add_scratches, name='new-team-scratch'),
    url(r'^team/(\d+)/scratches/view/', team_views.view_scratches, name='team-scratches'),
    url(r'^team/(\d+)/stats/', team_views.team_stats, name='team-stats'),
    url(r'^view_teams/$', team_views.view_teams, name='teams'),
    url(r'^enter_team/$', team_views.enter_team, name='new-team'),
    url(r'^all_tab_cards/$', team_views.all_tab_cards, name='tab-cards'),
    url(r'^team/card/(\d+)/$', team_views.tab_card, name='team-card'),
    url(r'^team/card/(\d+)/pretty/$', team_views.pretty_tab_card, name='pretty-team-card'),
    url(r'^team/ranking/$', team_views.rank_teams_ajax, name='team-rank-ajax'),
    url(r'^team/rank/$', team_views.rank_teams, name='team-rank'),

    # Debater related
    url(r'^debater/(\d+)/$', debater_views.view_debater, name='debater'),
    url(r'^debater/(\d+)/delete/$', debater_views.delete_debater, name='delete-debater'),
    url(r'^view_debaters/$', debater_views.view_debaters, name='debaters'),
    url(r'^enter_debater/$', debater_views.enter_debater, name='new-debater'),
    url(r'^debater/ranking/$', debater_views.rank_debaters_ajax, name='debater-rank-ajax'),
    url(r'^debater/rank/$', debater_views.rank_debaters, name='debater-rank'),

    # Pairing related
    url(r'^pairings/status/$', pairing_views.view_status, name='pairings-status'),
    url(r'^pairings/view_rounds/$', pairing_views.view_rounds, name='rounds'),
    url(r'^round/(\d+)/$', pairing_views.view_round, name='round'),
    url(r'^round/(\d+)/result/$', pairing_views.enter_result, name='new-round'),
    url(r'^round/(\d+)/result/(\d+)/$', pairing_views.enter_multiple_results, name='new-multi-round'),
    url(r'^round/(\d+)/alternative_judges/(\d+)/$', pairing_views.alternative_judges, name='round-alternate-judge'),
    url(r'^round/(\d+)/alternative_judges/$', pairing_views.alternative_judges, name='round-alternate-judges'),
    url(r'^round/(\d+)/assign_judge/(\d+)/$', pairing_views.assign_judge, name='round-assign-judge'),
    url(r'^round/(\d+)/assign_judge/(\d+)/(\d+)/$', pairing_views.assign_judge, name='round-assign-judges'),
    url(r'^pairing/pair_round/$', pairing_views.pair_round, name='pair-round'),
    url(r'^pairing/assign_judges/$', pairing_views.assign_judges_to_pairing, name='assign-judges'),
    url(r'^pairing/confirm_start_tourny/$', pairing_views.confirm_start_new_tourny, name='confirm-new-tournament'),
    url(r'^pairing/start_tourny/$', pairing_views.start_new_tourny, name='new-tournament'),
    url(r'^pairings/pairinglist/$', pairing_views.pretty_pair, name='pairings'),
    url(r'^pairings/pairinglist/printable/$', pairing_views.pretty_pair_print, name='print-pairings'),
    url(r'^pairing/backup/$', pairing_views.manual_backup, name='backup-pairing'),
    url(r'^pairing/released/$', pairing_views.get_pairing_released, name='release-pairing'),
    url(r'^pairing/release/$', pairing_views.toggle_pairing_released, name='toggle-release-pairing'),
    url(r'^pairing/view_backups/$', pairing_views.view_backups, name='pairing-backups'),
    url(r'^pairings/swap/(\d+)/(\d+)/with/(\d+)/(\d+)/$', pairing_views.swap_judges_in_round, name='swap-judges'),
    url(r'^pairings/swap_team/(\d+)/(\d+)/with/(\d+)/(\d+)/$', pairing_views.swap_teams_in_round, name='swap-teams'),


    # Backups
    url(r'^backup/restore/(.+)/$', pairing_views.restore_backup, name='restore-backup'),
    url(r'^backup/download/(.+)/$', pairing_views.download_backup, name='download-backup'),
    url(r'^backup/(.+)/$', pairing_views.view_backup, name='backup'),
    url(r'^upload_backup/$', pairing_views.upload_backup, name='upload-backup'),

    # Data Upload
    url(r'^import_data/$', views.upload_data, name='upload-data')
]
