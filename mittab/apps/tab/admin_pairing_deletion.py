"""Capture and repair pairing changes caused by staff admin cascades."""
from dataclasses import dataclass

from django.contrib.admin.utils import NestedObjects
from django.db import transaction
from django.db.models import Exists, OuterRef

from mittab.apps.tab.board_audit import (
    pairing_snapshot, record_pairing_change, record_pairing_removal, with_pairing_snapshot_relations,
)
from mittab.apps.tab.board_operations import recompute_seeds
from mittab.apps.tab.live_results import published_team_placements
from mittab.apps.tab.models import JudgeCodeEmailLog, Outround, PublishedOutroundStage, PublishedTeamPlacement, Round, WrittenRFDEmailLog
from mittab.libs.cacheing.cache_logic import refresh_rankings_after_commit
from mittab.libs.cacheing.public_cache import (
    invalidate_inround_public_pairings_cache, invalidate_outround_public_pairings_cache,
    invalidate_public_tab_cards_cache,
)


LATER_STAGE = "Resolve the later stage before deleting this pairing."


def later_stage_survives(removed):
    later = Outround.objects.using(removed.db).exclude(pk__in=removed).filter(
        type_of_round=OuterRef("type_of_round"), num_teams__lt=OuterRef("num_teams"),
    )
    return removed.filter(Exists(later)).exists()


def repair_removed_outrounds(actor, pairings):
    """Remove public placements for deleted matchups and recompute surviving seeds."""
    for pairing, before in pairings:
        record_pairing_removal(Outround, pairing.pk, before, actor)
    repair_public_placements(actor, [before for _, before in pairings])


def repair_public_placements(actor, snapshots):
    removed = {}
    for snapshot in snapshots:
        key = (snapshot["division"], snapshot["num_teams"])
        removed.setdefault(key, set()).update((snapshot["gov_team"]["id"], snapshot["opp_team"]["id"]))
    for (division, size), teams in removed.items():
        stages = PublishedOutroundStage.objects.filter(type_of_round=division, num_teams=size)
        remaining = list(Outround.objects.filter(type_of_round=division, num_teams=size)
                         .values_list("gov_team_id", "opp_team_id"))
        if remaining:
            survivors = {team for match in remaining for team in match}
            PublishedTeamPlacement.objects.filter(
                stage__in=stages, team_id__in=teams - survivors,
            ).delete()
        else:
            stages.delete()
    for division in sorted({division for division, _ in removed}):
        recompute_seeds(division, actor)
        published_team_placements(division)
        transaction.on_commit(lambda division=division: invalidate_outround_public_pairings_cache(division))



def _record_key(obj):
    return (obj._meta.label_lower, obj.pk) if obj is not None else ("", 0)


@dataclass
class RosterCascade:
    database: str
    before: dict
    removed: dict
    graph: list
    revisions: list

    @classmethod
    def capture(cls, selected, confirmed):
        collector = NestedObjects(using=selected.db, origin=selected)
        collector.collect(list(selected))
        if confirmed:
            for model, objects in sorted(collector.model_objs.items(), key=lambda item: item[0]._meta.label_lower):
                # Related inserts take a foreign-key lock on an already locked parent.
                ids = [obj.pk for obj in objects]
                list(model._base_manager.using(selected.db).filter(pk__in=ids)
                     .order_by("pk").select_for_update().values_list("pk", flat=True))
            collector = NestedObjects(using=selected.db, origin=selected)
            collector.collect(list(selected))
        graph = sorted((_record_key(parent), sorted(_record_key(child) for child in children))
                       for parent, children in collector.edges.items())
        removed = {}
        before = {}
        for model, link_field in ((Round, "round_id"), (Outround, "outround_id")):
            removed[model] = {obj.pk for obj in collector.model_objs.get(model, ())}
            affected = removed[model] | {getattr(link, link_field) for link in
                                       collector.model_objs.get(model.judges.through, ())}
            rows = model.objects.using(selected.db).filter(pk__in=affected).order_by("pk")
            if confirmed:
                list(rows.select_for_update().values_list("pk", flat=True))
            before[model] = {obj.pk: pairing_snapshot(obj) for obj in with_pairing_snapshot_relations(rows)}
            removed[model].intersection_update(before[model])

        revisions = [list(Round.objects.using(selected.db).filter(pk__in=removed[Round])
                          .order_by("pk").values_list("pk", "pullup", "result_entered_at", "rfd"))]
        for model in (JudgeCodeEmailLog, WrittenRFDEmailLog):
            ids = [obj.pk for obj in collector.model_objs.get(model, ())]
            fields = ("judge_id", "ballot_code", "source") if model is JudgeCodeEmailLog else ("round_id",)
            revisions.append(list(model.objects.using(selected.db).filter(pk__in=ids)
                                  .order_by("pk").values_list("pk", "sent_at", "email", "email_payload", *fields)))
        return cls(selected.db, before, removed, graph, revisions)

    def signature(self):
        snapshots = [sorted(self.before[model].items()) for model in (Round, Outround)]
        return self.graph, snapshots, self.revisions

    def finish(self, actor):
        removed_outrounds = []
        for model, records in self.before.items():
            surviving = with_pairing_snapshot_relations(model.objects.using(self.database).filter(pk__in=records))
            for obj in surviving.order_by("pk"):
                record_pairing_change(obj, records[obj.pk], actor, "admin deletion")
            for pk in sorted(self.removed[model]):
                snapshot = records[pk]
                record_pairing_removal(model, pk, snapshot, actor)
                if model is Outround:
                    removed_outrounds.append(snapshot)
        repair_public_placements(actor, removed_outrounds)
        if self.before[Round]:
            transaction.on_commit(invalidate_inround_public_pairings_cache, using=self.database)
            transaction.on_commit(invalidate_public_tab_cards_cache, using=self.database)
        deleted_divisions = {snapshot["division"] for snapshot in removed_outrounds}
        panel_divisions = {snapshot["division"] for snapshot in self.before[Outround].values()}
        for division in panel_divisions - deleted_divisions:
            transaction.on_commit(lambda division=division: invalidate_outround_public_pairings_cache(division),
                                  using=self.database)
        refresh_rankings_after_commit(using=self.database)
