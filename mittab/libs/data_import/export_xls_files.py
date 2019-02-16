from django.contrib.auth.decorators import permission_required
from django.http import HttpResponse
from xlwt import Workbook

from mittab.apps.tab.models import Team, Judge, Room, Debater
from mittab.libs import tab_logic


def _create_vn_str(novice_status):
    """Creates varsity-novice status string from the integer pseudo-enum used by the model"""
    for status, description in Debater.NOVICE_CHOICES:
        if status == novice_status: return status

    return 'NO STATUS'


def export_teams():
    """Exports teams as a new XLS file which is then streamed to an HTTP response. This file should always be
    cross-compatible with the parsing system used in the import_teams file. """

    book = Workbook('utf-8')
    sheet = book.add_sheet('Teams')

    # write headers
    headers = ['Name', 'School', 'Seed', 'Debater 1 Name', 'D1 Status', 'D1 Phone#', 'D1 Provider', 'Debater 2 Name',
               'D2 Status', 'D2 Phone#', 'D2 Provider']
    for i in xrange(len(headers)):
        sheet.write(0, i, headers[i])

    # write rows
    teams = Team.objects.all()
    for i in xrange(len(teams)):

        team = teams[i]
        row = i + 1

        name = team.name
        school = team.school.name

        # convert seed
        # seed = 0 if unseeded, seed = 1 if free seed, seed = 2 if half seed, seed = 3 if full seed
        seed = team.seed
        if seed is 0:
            seed = 'unseeded'
        elif seed is 1:
            seed = 'free'
        elif seed is 2:
            seed = 'half'
        elif seed is 3:
            seed = 'full'

        debaters = team.debaters.all()
        deb1_name = debaters[0].name
        deb1_status = _create_vn_str(debaters[0].novice_status)  # 0 = Varsity, 1 = Novice
        deb1_phone = debaters[0].phone
        deb1_provider = debaters[0].provider

        sheet.write(row, 0, name)
        sheet.write(row, 1, school)
        sheet.write(row, 2, seed)

        sheet.write(row, 3, deb1_name)
        sheet.write(row, 4, deb1_status)
        sheet.write(row, 5, deb1_phone)
        sheet.write(row, 6, deb1_provider)

        if len(debaters) > 1:
            deb2_name = debaters[1].name
            deb2_status = _create_vn_str(debaters[1].novice_status)
            deb2_phone = debaters[1].phone
            deb2_prov = debaters[1].provider

            sheet.write(row, 7, deb2_name)
            sheet.write(row, 8, deb2_status)
            sheet.write(row, 9, deb2_phone)
            sheet.write(row, 10, deb2_prov)

    return book


def export_judges():
    """Exports judges to a XLS file which is then streamed to the recipient. This method should always be
    cross-compatible with the import_judges file. """
    book = Workbook('utf-8')
    sheet = book.add_sheet('Judges')

    # 0 name, 1 rank, 2 phone, 3 provider, 4+ schools
    headers = ['Name', 'Rank', 'Phone', 'Provider', 'Schools']
    for i in xrange(len(headers)):
        sheet.write(0, i, headers[i])

    judges = Judge.objects.all()
    for i in xrange(len(judges)):
        judge = judges[i]
        row = i + 1

        name = judge.name
        rank = judge.rank
        phone = judge.phone
        provider = judge.provider
        schools = judge.schools.all()

        sheet.write(row, 0, name)
        sheet.write(row, 1, rank)
        sheet.write(row, 2, phone)
        sheet.write(row, 3, provider)

        for x in xrange(len(schools)):  # iterate through other school affiliations
            sheet.write(row, x + 4, schools[x].name)

    return book


@permission_required('tab.tab_settings.can_change', login_url="/403/")
def export_rooms():
    """Exports rooms to an XLS file which is then streamed to the recipient. This method should always be
    cross-compatible with the import_rooms file. """
    book = Workbook('utf-8')
    sheet = book.add_sheet('Rooms')

    # 0 name, 1 rank, 2 phone, 3 provider, 4+ schools
    headers = ['Name', 'Rank']
    for i in xrange(len(headers)):
        sheet.write(0, i, headers[i])

    rooms = Room.objects.all()
    for i in xrange(len(rooms)):
        room = rooms[i]
        row = i + 1
        sheet.write(row, 0, room.name)
        sheet.write(row, 1, room.rank)

    return book


@permission_required('tab.tab_settings.can_change', login_url="/403/")
def export_team_stats():
    """Exports data as XLS for each team: win-loss record, total speaker points, total ranks, single-adjusted
    speaker points, single-adjusted ranks, double-adjusted speaker points, double-adjusted ranks, and opposition
    strength."""
    book = Workbook('utf-8')
    sheet = book.add_sheet('Team stats')

    sheet.write(0, 0, 'If you are calculating the break off of these statistics, please pay attention to how you sort. '
                      'In general, everything except ranks should be sorted from large to small.')

    headers = ['team name', '# wins', 'total speaker points', 'total ranks', 'single-adjusted speaker points',
               'single-adjusted ranks', 'double-adjusted speaker points', 'double-adjusted ranks',
               'opposition strength']
    for i in xrange(len(headers)):
        sheet.write(1, i, headers[i])

    teams = Team.objects.all()
    for i in xrange(len(teams)):
        team, row = teams[i], i + 2
        sheet.write(row, 0, team.name)
        sheet.write(row, 1, tab_logic.tot_wins(team))
        sheet.write(row, 2, tab_logic.tot_speaks(team))
        sheet.write(row, 3, tab_logic.tot_ranks(team))
        sheet.write(row, 4, tab_logic.single_adjusted_speaks(team))
        sheet.write(row, 5, tab_logic.single_adjusted_ranks(team))
        sheet.write(row, 6, tab_logic.double_adjusted_speaks(team))
        sheet.write(row, 7, tab_logic.double_adjusted_ranks(team))
        sheet.write(row, 8, tab_logic.opp_strength(team))

    return book


@permission_required('tab.tab_settings.can_change', login_url="/403/")
def export_debater_stats():
    """Exports data as XLS for each speaker: total speaker points, total ranks, single adjusted speaks, single adjusted
    ranks, double adjusted speaks, double adjusted ranks, team performance, opposition strength. Automatically averages
    for iron-men. """
    book = Workbook('utf-8')
    sheet = book.add_sheet('Team stats')

    sheet.write(0, 0, 'If you are calculating the awards off these statistics, please pay attention to how you sort. '
                      'In general, everything except ranks should be sorted from large to small.')

    headers = ['debater name', 'total speaker points', 'total ranks', 'single-adjusted speaker points',
               'single-adjusted ranks', 'double-adjusted speaker points', 'double-adjusted ranks',
               'team performance (debater\'s team win#)', 'opposition strength']
    for i in xrange(len(headers)):
        sheet.write(1, i, headers[i])

    debaters = Debater.objects.all()
    for i in xrange(len(debaters)):
        debater, row = debaters[i], i + 2
        sheet.write(row, 0, debater.name)
        sheet.write(row, 1, tab_logic.tot_speaks_deb(debater, average_ironmen=True))
        sheet.write(row, 2, tab_logic.tot_ranks_deb(debater, True))
        sheet.write(row, 3, tab_logic.single_adjusted_speaks_deb(debater))
        sheet.write(row, 4, tab_logic.single_adjusted_ranks_deb(debater))
        sheet.write(row, 5, tab_logic.double_adjusted_speaks_deb(debater))
        sheet.write(row, 6, tab_logic.double_adjusted_ranks_deb(debater))
        sheet.write(row, 7, tab_logic.tot_wins(Team.objects.get(debaters__name=debater.name)))
        sheet.write(row, 8, tab_logic.opp_strength(debater))

    return book
