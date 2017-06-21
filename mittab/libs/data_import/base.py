from decimal import *
import xlrd
from xlwt import Workbook

from mittab.apps.tab.models import *
from mittab.apps.tab.forms import *


class DataImporter(object):
    num_columns = 1

    def __init__(self, file_to_import):
        self.worksheet = xlrd.open_workbook(filename=None,
                                            file_contents=file_to_import.read())
        self.errors = []
        try:
            self._value_at(0, self.num_columns - 1)
        except:
            self.errors.append('ERROR: Insufficient columns in sheet. No data read.')

    # TODO: Avoid repeated calcuations
    def num_rows(self):
        num_rows = 0
        found_end = False

        while not found_end:
            try:
                self.worksheet.cell(num_rows, 0).value.strip()
                num_rows += 1
            except:
                found_end = True

        return num_rows

    def save(self):
        for i in range(1, self.num_rows()):
            self.read_row(i)

    def read_row(self, row_num):
        None

    def _value_at(row, column):
        return self.worksheet.cell(row, column).value


class JudgeImporter(DataImporter):
    num_columns = 2

    def read_row(self, row_num):
        #Load and validate Judge's Name
        judge_name = self._value_at(row_num, 0)

        try:
            Judge.objects.get(name=judge_name)
            self.errors.append(judge_name + ": Duplicate Judge Name")
        except:
            return

        #Load and validate judge_rank
        judge_rank = self._value_at(row_num, 1)
        try:
            judge_rank = round(float(judge_rank), 2)
        except:
            self.errors.append(judge_name + ": Rank not number")

        if judge_rank > 100 or judge_rank < 0:
            self.errors.append(judge_name + ": Rank should be between 0-100")

        #Because this data is not required, be prepared for IndexErrors
        try:
            judge_phone = str(int(self._value_at(row_num, 2)))
        except IndexError:
            judge_phone = ''
        try: 
            judge_provider = self._value_at(row_num, 3)
        except IndexError:
            judge_provider = ''

        #iterate through schools until none are left
        col_num = 4
        schools = []
        while(True):
            try:
                judge_school = sh._value_at(row_num, col_num)
                #If other judges have more schools but this judge doesn't, we get an empty string
                #If blank, keep iterating in case user has a random blank column for some reason
                if judge_school != '':
                    try:
                        #Get id from the name because JudgeForm requires we use id
                        s = School.objects.get(name__iexact=judge_school).id 
                        schools.append(s)
                    except IndexError:
                        break
                    except:
                        try:
                            s = School(name=judge_school)
                            s.save()
                            schools.append(s.id)
                        except:
                            self.errors.append(judge_name + ': Invalid School')
                            return
            except IndexError:
                break
            col_num += 1

        data = {'name': judge_name, 'rank': judge_rank, 'phone': judge_phone, 'provider': judge_provider, 'schools': schools}
        form = JudgeForm(data=data)
        if (form.is_valid()):
            form.save()
        else:
            error_messages = sum([ error[1] for error in form.errors.items() ], [])
            error_string = ', '.join(error_messages)
            self.errors.append("%s: %s" % (judge_name, error_string))


class TeamImporter(DataImporter):
    num_columns = 9

    def read_row(self, row_num):
        team_name = self._value_at(row_num, 0)
        school_name = self._value_at(row_num, 1)
        try:
            team_school = School.objects.get(name__iexact=school_name)
        except:
            #Create school through SchoolForm because for some reason they don't save otherwise
            form = SchoolForm(data={'name': school_name})
            if form.is_valid():
                form.save()
            else:
                self.errors.append(team_name + ": Invalid School")
                return
            team_school = School.objects.get(name__iexact=school_name)


        #TODO: Verify there are not multiple free seeds from the same school
        team_seed = self._value_at(row_num, 2).lower()
        if team_seed in ['full_seed', 'full']:
            team_seed = 3
        elif team_seed in ['half seed', 'half']:
            team_seed = 2
        elif team_seed in ['free seed', 'free']:
            team_seed = 1
        elif team_seed in ['unseeded', 'un', 'none', '']:
            team_seed = 0
        else:
            self.errors.append(team_name + ': Invalid Seed Value')
            return

        deb1_name = self._value_at(row_num, 3)
        if deb1_name == '':
            self.errors.append(team_name + ': Empty Debater-1 Name')
            return
        try:
            Debater.objects.get(name=deb1_name)
            self.errors.append(team_name + ': Duplicate Debater-1 Name')
            return
        except:
            pass

        deb1_status = self._value_at(row_num, 4).lower()

        if deb1_status in ['novice', 'nov', 'n']:
            deb1_status = 1
        else:
            deb1_status = 0

        deb1_phone = self._value_at(row_num, 5)
        deb1_provider = self._value_at(row_num, 6)


        deb2_name = self._value_at(row_num, 7)
        iron_man = deb2_name == ''

        if not iron_man:
            try:
                Debater.objects.get(name=deb2_name)
                self.errors.append(team_name + ': Duplicate Debater-2 Name')
                return
            except:
                pass

            deb2_status = self._value_at(row_num, 8).lower()
            if deb2_status in ['novice', 'nov', 'n']:
                deb2_status = 1
            else:
                deb2_status = 0

            #Since this is not required data and at the end of the sheet, be ready for index errors
            try: 
                deb2_phone = self._value_at(row_num, 9)
            except IndexError:
                deb2_phone = ''
            try:
                deb2_provider = self._value_at(row_num, 10)
            except IndexError:
                deb2_provider = ''


        #Save Everything
        try:
            deb1 = Debater(name=deb1_name, novice_status=deb1_status, phone=deb1_phone, provider=deb1_provider)
            deb1.save()
        except:
            self.errors.append(team_name + ': Unkown Error Saving Debater 1')
            return

        if not iron_man:
            try:
                deb2 = Debater(name=deb2_name, novice_status=deb2_status, phone=deb2_phone, provider=deb2_provider)
                deb2.save()
            except:
                # TODO: Make this not do that...
                self.errors.append(team_name + ': Unkown Error Saving Debater 2')
                self.errors.append('        WARNING: Debaters on this team may be added to database. ' +
                                    'Please Check this Manually')
                return

        team = Team(name=team_name, school=team_school, seed=team_seed)
        try:
            team.save()
            team.debaters.add(deb1)
            if not iron_man:
                team.debaters.add(deb2)
            else:
                self.errors.append(team_name + ": Detected to be Iron Man - Still added successfully")
            team.save()
        except:
            self.errors.append(team_name + ': Unknown Error Saving Team')
            self.errors.append('        WARNING: Debaters on this team may be added to database. ' +
                                'Please Check this Manually')
