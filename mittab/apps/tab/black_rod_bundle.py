import json
from datetime import datetime, timezone

from mittab.apps.tab.models import Debater, Judge, Outround, Round, School, Team


class BlackRodBundleExporter:
    SCHEMA_VERSION = 1
    SOURCE = "mit_tab_black_rod_bundle"

    def __init__(self, tournament_name):
        self.tournament_name = tournament_name
        self._school_by_debater_id = None

    def export_tournament(self):
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "source": self.SOURCE,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "tournament_name": self.tournament_name,
            "schools": self._build_schools(),
            "debaters": self._build_debaters(),
            "rounds": self._build_rounds(),
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def _build_schools(self):
        return [
            {
                "id": school.id,
                "apda_id": self._normalize_apda_id(school.apda_id),
                "name": school.name,
            }
            for school in School.objects.order_by("id")
        ]

    def _build_debaters(self):
        school_by_debater_id = self._build_school_by_debater_id()
        return [
            {
                "id": debater.id,
                "apda_id": self._normalize_apda_id(debater.apda_id),
                "name": debater.name,
                "novice_status": self._division_for_debater(debater),
                "school_id": school_by_debater_id.get(debater.id),
            }
            for debater in Debater.objects.order_by("id")
        ]

    def _build_rounds(self):
        rounds = []
        rounds.extend(self._build_prelim_rounds())
        rounds.extend(self._build_outrounds())
        return sorted(
            rounds,
            key=lambda round_row: (
                0 if round_row["stage"] == "prelim" else 1,
                int(round_row["round_number"]),
                str(round_row["import_key"]),
            ),
        )

    def _build_prelim_rounds(self):
        qs = (
            Round.objects.order_by("round_number", "id")
            .select_related("gov_team", "opp_team", "chair")
            .prefetch_related("judges")
        )
        return [
            {
                "import_key": f"prelim:{round_obj.id}",
                "round_number": round_obj.round_number,
                "label": f"Round {round_obj.round_number}",
                "stage": "prelim",
                "division": None,
                "elim_size": None,
                "victor": round_obj.victor,
                "gov": self._serialize_team(round_obj.gov_team),
                "opp": self._serialize_team(round_obj.opp_team),
                "judges": self._serialize_judges(round_obj.chair, round_obj.judges.all()),
            }
            for round_obj in qs
        ]

    def _build_outrounds(self):
        qs = (
            Outround.objects.order_by("num_teams", "type_of_round", "id")
            .select_related("gov_team", "opp_team", "chair")
            .prefetch_related("judges")
        )
        return [
            {
                "import_key": (
                    f"outround:{self._division_for_outround(outround)}:"
                    f"{outround.num_teams}:{outround.id}"
                ),
                "round_number": outround.num_teams,
                "label": self._label_for_outround(outround),
                "stage": "outround",
                "division": self._division_for_outround(outround),
                "elim_size": outround.num_teams,
                "victor": outround.victor,
                "gov": self._serialize_team(outround.gov_team),
                "opp": self._serialize_team(outround.opp_team),
                "judges": self._serialize_judges(outround.chair, outround.judges.all()),
            }
            for outround in qs
        ]

    def _serialize_team(self, team):
        debaters = list(team.debaters.order_by("id"))
        return {
            "debater_ids": [debater.id for debater in debaters],
            "source_names": [debater.name for debater in debaters],
        }

    @staticmethod
    def _serialize_judges(chair, judges):
        judge_rows = []
        seen_ids = set()

        if chair is not None:
            judge_rows.append({"original_name": chair.name, "is_chair": True})
            seen_ids.add(chair.id)

        for judge in sorted(judges, key=lambda value: value.id):
            if judge.id in seen_ids:
                continue
            judge_rows.append({"original_name": judge.name, "is_chair": False})
            seen_ids.add(judge.id)

        return judge_rows

    def _build_school_by_debater_id(self):
        if self._school_by_debater_id is not None:
            return self._school_by_debater_id

        school_by_debater_id = {}
        teams = Team.objects.select_related("school", "hybrid_school").prefetch_related("debaters")
        for team in teams.order_by("id"):
            debaters = list(team.debaters.order_by("id"))
            if not debaters:
                continue

            if team.hybrid_school_id and len(debaters) >= 2:
                school_by_debater_id.setdefault(debaters[0].id, team.school_id)
                school_by_debater_id.setdefault(debaters[1].id, team.hybrid_school_id)
                for debater in debaters[2:]:
                    school_by_debater_id.setdefault(debater.id, team.school_id)
                continue

            for debater in debaters:
                school_by_debater_id.setdefault(debater.id, team.school_id)

        self._school_by_debater_id = school_by_debater_id
        return self._school_by_debater_id

    @staticmethod
    def _normalize_apda_id(value):
        if value in (None, "", -1):
            return None
        return int(value)

    @staticmethod
    def _division_for_debater(debater):
        return "novice" if debater.novice_status == Debater.NOVICE else "varsity"

    @staticmethod
    def _division_for_outround(outround):
        return "novice" if outround.type_of_round == Outround.NOVICE else "varsity"

    def _label_for_outround(self, outround):
        division = "Novice" if outround.type_of_round == Outround.NOVICE else "Varsity"
        return f"{division} {self._elim_label(outround.num_teams)}"

    @staticmethod
    def _elim_label(num_teams):
        labels = {
            2: "Final",
            4: "Semifinal",
            8: "Quarterfinal",
            16: "Octofinal",
            32: "Double-Octofinal",
            64: "Triple-Octofinal",
        }
        return labels.get(num_teams, f"Elim of {num_teams}")
