from xml.etree.ElementTree import Element, SubElement, tostring

from mittab.apps.tab.models import School, Team, Judge, Room, Round

DEBATE_ID_PREFIX = "D"
ROOM_ID_PREFIX = "V"
SPEAKER_ID_PREFIX = "S"
TEAM_ID_PREFIX = "T"
JUDGE_ID_PREFIX = "A"
SCHOOL_ID_PREFIX = "I"
SPEAKER_STATUS_PREFIX = "SC"


class ArchiveExporter:

    def __init__(self, name):
        self.name = name
        self.root = None

    def export_tournament(self):
        self.root = Element("tournament", {"name": self.name, "style": "apda"})

        self.add_rounds()
        self.add_participants()
        self.add_schools()
        self.add_rooms()
        self.add_categories()

        return tostring(self.root)

    def add_rounds(self):
        pos = ["pm", "lo", "mg", "mo"]
        qs = Round.objects.all().order_by("round_number").prefetch_related(
            "judges", "roundstats_set")

        cur_round = None
        r_tag = None
        for debate in qs:
            if debate.round_number != cur_round:
                r_tag = SubElement(self.root, "round", {
                    "name": "Round %s" % debate.round_number
                })
                cur_round = debate.round_number

            adjs = " ".join(
                [JUDGE_ID_PREFIX + str(j.id) for j in debate.judges.all()])
            d_tag = SubElement(r_tag, "debate", {
                "id": DEBATE_ID_PREFIX + str(debate.id),
                "chair": JUDGE_ID_PREFIX + str(debate.chair_id),
                "adjudicators": adjs,
                "venue": ROOM_ID_PREFIX + str(debate.room_id)
            })

            gov_tag = SubElement(d_tag, "side", {
                "team": TEAM_ID_PREFIX + str(debate.gov_team_id)
            })
            opp_tag = SubElement(d_tag, "side", {
                "team": TEAM_ID_PREFIX + str(debate.opp_team_id)
            })

            if debate.victor == Round.GOV_VIA_FORFEIT or \
                    debate.victor == Round.ALL_DROP:
                opp_tag.set("forfeit", "true")
            elif debate.victor == Round.OPP_VIA_FORFEIT or \
                    debate.victor == Round.ALL_DROP:
                gov_tag.set("forfeit", "true")

            team_points = [0, 0]  # Gov, Opp
            stats = sorted(list(debate.roundstats_set.all().values()),
                           key=lambda x: pos.index(x["debater_role"]))

            if debate.victor == Round.GOV or debate.victor == Round.OPP:
                for i, speech in enumerate(stats):
                    side_tag = gov_tag if i % 2 == 0 else opp_tag
                    team_points[i % 2] += speech["speaks"]
                    speech_tag = SubElement(side_tag, "speech", {
                        "speaker": SPEAKER_ID_PREFIX + str(speech["debater_id"]),
                        "reply": "false"
                    })
                    ballot = SubElement(speech_tag, "ballot", {
                        "adjudicators": adjs,
                        "rank": str(int(speech["ranks"])),
                        "ignored": "false"
                    })
                    ballot.text = str(speech["speaks"])

            gov_win = [Round.GOV, Round.GOV_VIA_FORFEIT, Round.ALL_WIN]
            gov_rank = "1" if debate.victor in gov_win else "2"
            gov_ballot = Element("ballot", {
                "adjudicators": adjs, "rank": gov_rank})
            gov_ballot.text = str(team_points[0])
            gov_tag.insert(0, gov_ballot)  # Ballot must be first in sequence

            opp_win = [Round.OPP, Round.OPP_VIA_FORFEIT, Round.ALL_WIN]
            opp_rank = "1" if debate.victor in opp_win else "2"
            opp_ballot = Element("ballot", {
                "adjudicators": adjs, "rank": opp_rank})
            opp_ballot.text = str(team_points[1])
            opp_tag.insert(0, opp_ballot)

    def add_participants(self):
        participants_tag = SubElement(self.root, "participants")

        for team in Team.objects.all().prefetch_related("debaters"):
            team_tag = SubElement(participants_tag, "team", {
                "id": TEAM_ID_PREFIX + str(team.id),
                "name": team.name
            })
            if team.team_code is not None:
                team_tag.set("code", team.team_code)

            institutions = SCHOOL_ID_PREFIX + str(team.school_id)
            if team.hybrid_school_id is not None:
                institutions += " " + SCHOOL_ID_PREFIX + str(team.hybrid_school_id)

            for debater in team.debaters.all():
                debater_tag = SubElement(team_tag, "speaker", {
                    "id": SPEAKER_ID_PREFIX + str(debater.id),
                    "categories": SPEAKER_STATUS_PREFIX + str(debater.novice_status),
                    "institutions": institutions
                })
                debater_tag.text = debater.name

        for judge in Judge.objects.all().prefetch_related("schools"):
            SubElement(participants_tag, "adjudicator", {
                "id": JUDGE_ID_PREFIX + str(judge.id),
                "name": judge.name,
                "score": str(judge.rank),
                "institutions": " ".join(
                    [SCHOOL_ID_PREFIX + str(s.id) for s in judge.schools.all()])
            })

    def add_schools(self):
        for school in School.objects.all():
            school_tag = SubElement(self.root, "institution", {
                "id": SCHOOL_ID_PREFIX + str(school.id),
                "reference": school.name
            })
            school_tag.text = school.name

    def add_rooms(self):
        for room in Room.objects.all():
            room_tag = SubElement(self.root, "venue", {
                "id": ROOM_ID_PREFIX + str(room.id),
                "score": str(room.rank)
            })
            room_tag.text = room.name

    def add_categories(self):
        for i, name in enumerate(["Varsity", "Novice"]):
            sc_tag = SubElement(self.root, "speaker-category", {
                "id": SPEAKER_STATUS_PREFIX + str(i)
            })
            sc_tag.text = name
