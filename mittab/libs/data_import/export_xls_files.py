from django.contrib.auth.decorators import permission_required
from django.http import HttpResponse

# data stuff
from xlwt import Workbook
import pandas as pd

from mittab.apps.tab.models import Team, Judge, Room, Debater
from mittab.libs import tab_logic


def _vn_status_to_str(novice_status):
    """Creates varsity-novice status string from the integer pseudo-enum used by the model"""
    try:
        return next(description for i, description in Debater.NOVICE_CHOICES if novice_status == i)
    except StopIteration:
        return 'NO STATUS'


def _seed_to_str(seed_int):
    """Creates seed status string from the integer pseudo-enum used by the model"""
    try:
        return next(description for i, description in Team.SEED_CHOICES if seed_int == i)
    except StopIteration:
        return 'NO SEED'


def export_teams_df():
    """Exports teams as a new XLS file which is then streamed to an HTTP response. This file should always be
    cross-compatible with the parsing system used in the import_teams file. """

    entries = []
    for i, team in enumerate(Team.objects.all()):
        entry = {
            'team_name': team.name,
            'team_school': team.school.name,
            'team_seed': _seed_to_str(team.seed)
        }

        for debater_count, debater in enumerate(team.debaters.all()):
            root = 'debater_{}_'.format(debater_count + 1)
            entry[root + 'name'] = debater.name
            entry[root + 'status'] = _vn_status_to_str(debater.novice_status)
            entry[root + 'phone'] = debater.phone
            entry[root + 'provider'] = debater.provider

        entries.append(entry)

    return pd.DataFrame(entries)


def export_teams():
    """Exports teams as a new XLS file which is then streamed to an HTTP response. This file should always be
    cross-compatible with the parsing system used in the import_teams file. """

    book = Workbook('utf-8')
    sheet = book.add_sheet('Teams')

    # write headers
    headers = ['team_name', 'team_school', 'team_seed', 'team_debater_1_name', 'team_debater_1_status',
               'team_debater_1_phone', 'team_debater_1_provider', 'team_debater_2_name',
               'team_debater_2_status', 'team_debater_2_phone', 'team_debater_2_provider']
    for i, header in enumerate(headers):
        sheet.write(0, i, header)

    # write rows
    for i, team in enumerate(Team.objects.all()):
        row = i + 1

        name = team.name
        school = team.school.name

        # convert seed
        # seed = 0 if unseeded, seed = 1 if free seed, seed = 2 if half seed, seed = 3 if full seed
        seed = _seed_to_str(team.seed)

        debaters = team.debaters.all()
        deb1_name = debaters[0].name
        deb1_status = _vn_status_to_str(debaters[0].novice_status)  # 0 = Varsity, 1 = Novice
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
            deb2_status = _vn_status_to_str(debaters[1].novice_status)
            deb2_phone = debaters[1].phone
            deb2_prov = debaters[1].provider

            sheet.write(row, 7, deb2_name)
            sheet.write(row, 8, deb2_status)
            sheet.write(row, 9, deb2_phone)
            sheet.write(row, 10, deb2_prov)

    return book


def export_judges_df():
    entries = []
    for i, judge in enumerate(Judge.objects.all()):
        entry = {
            'judge_name': judge.name,
            'judge_rank': judge.rank,
            'judge_phone': judge.phone,
            'judge_provider': judge.provider
        }

        for judge_i, school in enumerate(judge.schools.all()):
            entry['judge_school_{}'.format(judge_i + 1)] = school.name

        entries.append(entry)

    df = pd.DataFrame(entries)
    return df


def export_judges():
    """Exports judges to a XLS file which is then streamed to the recipient. This method should always be
    cross-compatible with the import_judges file. """
    book = Workbook('utf-8')
    sheet = book.add_sheet('Judges')

    # 0 name, 1 rank, 2 phone, 3 provider, 4+ schools
    headers = ['judge_name', 'judge_rank', 'judge_phone', 'judge_provider', 'judge_schools']
    for i, header in enumerate(headers):
        sheet.write(0, i, header)

    for i, judge in enumerate(Judge.objects.all()):
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

        for j, school in enumerate(schools):  # iterate through other school affiliations
            sheet.write(row, j + 4, school.name)

    return book


def export_rooms_df():
    entries = []
    for i, room in enumerate(Room.objects.all()):
        entry = {
            'room_name': room.name,
            'room_rank': room.rank
        }
        entries.append(entry)

    return pd.DataFrame(entries)


def export_rooms():
    """Exports rooms to an XLS file which is then streamed to the recipient. This method should always be
    cross-compatible with the import_rooms file. """
    book = Workbook('utf-8')
    sheet = book.add_sheet('Rooms')

    # 0 name, 1 rank, 2 phone, 3 provider, 4+ schools
    headers = ['room_name', 'room_rank']
    for i, header in enumerate(headers):
        sheet.write(1, i, header)

    for i, room in enumerate(Room.objects.all()):
        row = i + 1
        sheet.write(row, 0, room.name)
        sheet.write(row, 1, room.rank)

    return book


def export_team_stats_df():
    entries = []
    for team in Team.objects.all():
        entries.append({
            'team_name': team.name,
            'team_wins': tab_logic.tot_wins(team),
            'team_speaks': tab_logic.tot_speaks(team),
            'team_ranks': tab_logic.tot_ranks(team),
            'team_speaks_single_adj': tab_logic.single_adjusted_speaks(team),
            'team_ranks_single_adj': tab_logic.single_adjusted_ranks(team),
            'team_speaks_double_adj': tab_logic.double_adjusted_speaks(team),
            'team_ranks_double_adj': tab_logic.double_adjusted_ranks(team),
            'team_opp_str': tab_logic.opp_strength(team)
        })

    return pd.DataFrame(entries)


def export_team_stats():
    """Exports data as XLS for each team: win-loss record, total speaker points, total ranks, single-adjusted
    speaker points, single-adjusted ranks, double-adjusted speaker points, double-adjusted ranks, and opposition
    strength."""
    book = Workbook('utf-8')
    sheet = book.add_sheet('Team stats')

    sheet.write(0, 0,
                'If you are calculating the break off of these statistics, please pay attention to how you sort. '
                'In general, everything except ranks should be sorted from large to small.')

    headers = ['team_name', 'team_wins', 'team_speaks', 'team_ranks', 'team_speaks_single_adj',
               'team_ranks_single_adj', 'team_speaks_double_adj', 'team_ranks_double_adj', 'team_opp_str']
    for i, header in enumerate(headers):
        sheet.write(1, i, header)

    for i, team in enumerate(Team.objects.all()):
        row = i + 2

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


def export_debater_stats_df():
    entries = []
    for debater in Debater.objects.all():
        debater_team = Team.objects.get(debaters=debater)
        entries.append({
            'debater_name': debater.name,
            'debater_speaks': tab_logic.tot_speaks_deb(debater, average_ironmen=True),
            'debater_ranks': tab_logic.tot_ranks_deb(debater, True),
            'debater_speaks_single_adj': tab_logic.single_adjusted_speaks_deb(debater),
            'debater_ranks_single_adj': tab_logic.single_adjusted_ranks_deb(debater),
            'debater_speaks_double_adj': tab_logic.double_adjusted_speaks_deb(debater),
            'debater_ranks_double_adj': tab_logic.double_adjusted_ranks_deb(debater),
            'debater_team_wins': tab_logic.tot_wins(debater_team),
            'debater_team_opp_strength': tab_logic.opp_strength(debater_team)
        })

    return pd.DataFrame(entries)


def export_debater_stats():
    """Exports data as XLS for each speaker: total speaker points, total ranks, single adjusted speaks, single adjusted
    ranks, double adjusted speaks, double adjusted ranks, team performance, opposition strength. Automatically averages
    for iron-men. """
    book = Workbook('utf-8')
    sheet = book.add_sheet('Team stats')

    sheet.write(0, 0,
                'If you are calculating the awards off these statistics, please pay attention to how you sort. '
                'In general, everything except ranks should be sorted from large to small.')

    headers = ['debater_name', 'debater_speaks', 'debater_ranks', 'debater_speaks_single_adj',
               'debater_ranks_single_adj', 'debater_speaks_double_adj', 'debater_ranks_double_adj', 'debater_team_wins',
               'debater_team_opp_strength']
    for i, header in enumerate(headers):
        sheet.write(1, i, header)

    for i, debater in enumerate(Debater.objects.all()):
        row = i + 2
        debater_team = Team.objects.get(debaters=debater)

        sheet.write(row, 0, debater.name)
        sheet.write(row, 1, tab_logic.tot_speaks_deb(debater, average_ironmen=True))
        sheet.write(row, 2, tab_logic.tot_ranks_deb(debater, True))
        sheet.write(row, 3, tab_logic.single_adjusted_speaks_deb(debater))
        sheet.write(row, 4, tab_logic.single_adjusted_ranks_deb(debater))
        sheet.write(row, 5, tab_logic.double_adjusted_speaks_deb(debater))
        sheet.write(row, 6, tab_logic.double_adjusted_ranks_deb(debater))

        # debater stats
        sheet.write(row, 7, tab_logic.tot_wins(debater_team))
        sheet.write(row, 8, tab_logic.opp_strength(debater_team))

    return book
