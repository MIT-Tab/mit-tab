from django.views import i18n
from django.conf.urls import include, url
from django.contrib.auth.views import logout
from django.conf import settings
import apps.tab.views as views
import apps.tab.judge_views as judge_views
import apps.tab.team_views as team_views
import apps.tab.debater_views as debater_views
import apps.tab.pairing_views as pairing_views
from mittab.apps.tab import template_views
from mittab.libs.data_import import export_xls_files

from django.contrib import admin

admin.autodiscover()

urlpatterns = [
    # Uncomment the admin/doc line below to enable admin documentation:
    # url(r'^admin/doc/', include('django.contrib.admindocs.urls')),

    url(r'^admin/', include(admin.site.urls)),
    url(r'^dynamic-media/jsi18n/$', i18n.javascript_catalog),

    url(r'^$', views.index),
    url(r'^403/', views.render_403),

    # TODO: named urls
    # Account related
    url(r'^accounts/login/$', views.tab_login),
    url(r'^accounts/logout/$', logout),

    # Judge related
    url(r'^judge/(\d+)/$', judge_views.view_judge),
    url(r'^judge/(\d+)/delete/$', judge_views.delete_judge),
    url(r'^judge/(\d+)/scratches/add/(\d+)/', judge_views.add_scratches),
    url(r'^judge/(\d+)/scratches/view/', judge_views.view_scratches),
    url(r'^view_judges/$', judge_views.view_judges),
    url(r'^enter_judge/$', judge_views.enter_judge),

    # School related
    url(r'^school/(\d+)/$', views.view_school),
    url(r'^school/(\d+)/delete/$', views.delete_school),
    url(r'^view_schools/$', views.view_schools),
    url(r'^enter_school/$', views.enter_school),

    # Room related
    url(r'^room/(\d+)/$', views.view_room),
    url(r'^room/(\d+)/delete/$', views.delete_room),
    url(r'^view_rooms/$', views.view_rooms),
    url(r'^enter_room/$', views.enter_room),

    # Scratch related
    url(r'^judge/(\d+)/scratches/delete/(\d+)/', views.delete_scratch),
    url(r'^team/(\d+)/scratches/delete/(\d+)/', views.delete_scratch),
    url(r'^scratches/view/', views.view_scratches),
    url(r'^enter_scratch/', views.add_scratch),

    # Team related
    url(r'^team/(\d+)/$', team_views.view_team),
    url(r'^team/(\d+)/delete/$', team_views.delete_team),
    url(r'^team/(\d+)/scratches/add/(\d+)/', team_views.add_scratches),
    url(r'^team/(\d+)/scratches/view/', team_views.view_scratches),
    url(r'^team/(\d+)/stats/', team_views.team_stats),
    url(r'^view_teams/$', team_views.view_teams),
    url(r'^enter_team/$', team_views.enter_team),
    url(r'^all_tab_cards/$', team_views.all_tab_cards),
    url(r'^team/card/(\d+)/$', team_views.tab_card),
    url(r'^team/card/(\d+)/pretty/$', team_views.pretty_tab_card),
    url(r'^team/ranking/$', team_views.rank_teams_ajax),
    url(r'^team/rank/$', team_views.rank_teams),

    # Debater related
    url(r'^debater/(\d+)/$', debater_views.view_debater),
    url(r'^debater/(\d+)/delete/$', debater_views.delete_debater),
    url(r'^view_debaters/$', debater_views.view_debaters),
    url(r'^enter_debater/$', debater_views.enter_debater),
    url(r'^debater/ranking/$', debater_views.rank_debaters_ajax),
    url(r'^debater/rank/$', debater_views.rank_debaters),

    # Pairing related
    url(r'^pairings/status/$', pairing_views.view_status),
    url(r'^pairings/view_rounds/$', pairing_views.view_rounds),
    url(r'^round/(\d+)/$', pairing_views.view_round),
    url(r'^round/(\d+)/result/$', pairing_views.enter_result),
    url(r'^round/(\d+)/result/(\d+)/$', pairing_views.enter_multiple_results),
    url(r'^round/(\d+)/alternative_judges/(\d+)/$', pairing_views.alternative_judges),
    url(r'^round/(\d+)/alternative_judges/$', pairing_views.alternative_judges),
    url(r'^round/(\d+)/assign_judge/(\d+)/$', pairing_views.assign_judge),
    url(r'^round/(\d+)/assign_judge/(\d+)/(\d+)/$', pairing_views.assign_judge),
    url(r'^pairing/pair_round/$', pairing_views.pair_round),
    url(r'^pairing/assign_judges/$', pairing_views.assign_judges_to_pairing),
    url(r'^pairing/confirm_start_tourny/$', pairing_views.confirm_start_new_tourny),
    url(r'^pairing/start_tourny/$', pairing_views.start_new_tourny),
    url(r'^pairings/pairinglist/$', pairing_views.pretty_pair),
    url(r'^pairings/pairinglist/printable/$', pairing_views.pretty_pair_print),
    url(r'^pairing/backup/$', pairing_views.manual_backup),
    url(r'^pairing/release/$', pairing_views.toggle_pairing_released),
    url(r'^pairing/view_backups/$', pairing_views.view_backups),
    url(r'^pairings/swap/(\d+)/(\d+)/with/(\d+)/(\d+)/$', pairing_views.swap_judges_in_round),
    url(r'^pairings/swap_team/(\d+)/(\d+)/with/(\d+)/(\d+)/$', pairing_views.swap_teams_in_round),

    # Backups
    url(r'^backup/restore/(.+)/$', pairing_views.restore_backup),
    url(r'^backup/download/(.+)/$', pairing_views.download_backup),
    url(r'^backup/(.+)/$', pairing_views.view_backup),
    url(r'^upload_backup/$', pairing_views.upload_backup),

    # Data upload and download
    url(r'^import_data/$', views.upload_data, name='upload-data'),
    url(r'^export/$', template_views.export_xls_portal, name='xls-export'),
    url(r'^export/teams', template_views.export_team_xls, name='xls-teams'),
    url(r'^export/judges', template_views.export_judge_xls, name='xls-judges'),
    url(r'^export/rooms', template_views.export_room_xls, name='xls-rooms'),
    url(r'^export/team-stats', template_views.export_team_stats_xls, name='xls-team-stats'),
    url(r'^export/speaker-stats', template_views.export_debater_stats_xls, name='xls-debater-stats')
]
