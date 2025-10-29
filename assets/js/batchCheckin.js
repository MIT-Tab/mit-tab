import $ from "jquery";
import "../css/batchCheckin.scss";

const submitCheckIn = (checkboxes, checked) => {
  const $boxes = $(checkboxes);
  if (!$boxes.length) return;

  const entityType = $boxes
    .first()
    .closest("[data-entity-type]")
    .data("entityType");
  const ids = [...new Set($boxes.map((_, el) => $(el).data("id")).get())];
  const rounds = $boxes.map((_, el) => $(el).data("round")).get();

  $.post("/bulk_check_in/", {
    csrfmiddlewaretoken: $("[name=csrfmiddlewaretoken]").val(),
    entity_type: entityType,
    action: checked ? "check_in" : "check_out",
    entity_ids: ids,
    rounds: [...new Set(rounds)].filter((r) => r != null),
  })
    .done(() => {
      const status = checked ? "In" : "Out";
      $boxes.each((_, cb) =>
        $(cb).prop("checked", checked).next("label").text(`Checked ${status}`),
      );
    })
    .fail(() => alert("Check-in failed. Please try again."));
};

const getBulkTargets = (btn) => {
  const { scope, round } = $(btn).data();
  const $btn = $(btn);
  const entityType = $btn.closest("[data-entity-type]").data("entityType");
  const selector = `.checkin-toggle`;

  if (scope === "row") return $btn.closest("tr:visible").find(selector);

  return $btn
    .closest(".tab-pane")
    .find(selector)
    .filter((_, el) => {
      const $el = $(el);
      const isVisible = $el.closest("tr").is(":visible");
      const matchesRound =
        entityType === "team" || round == null || $el.data("round") === round;
      return matchesRound && isVisible;
    });
};

$(() => {
  let drag = null;
  const toggleSelector = ".checkin-toggle";

  $(".tab-pane table")
    .on("mousedown", "td, th", (e) => {
      const $cell = $(e.currentTarget);
      if (!$cell.find(toggleSelector).length) return;
      if (!$cell.closest("tr").is(":visible")) return;

      drag = {
        start: $cell,
        toggle: !$cell.find(".checkin-toggle").prop("checked"),
      };
      $cell.addClass("drag-selecting");
      e.preventDefault();
    })
    .on("mouseenter", "td, th", (e) => {
      if (!drag) return;

      const $end = $(e.currentTarget);
      const $rows = drag.start.closest("table").find("tr:visible");
      const getIndex = ($c) => $c.parent().children().index($c);
      const [r1, c1, r2, c2] = [
        $rows.index(drag.start.parent()),
        getIndex(drag.start),
        $rows.index($end.parent()),
        getIndex($end),
      ];

      $(".drag-selecting").removeClass("drag-selecting");
      $rows.slice(Math.min(r1, r2), Math.max(r1, r2) + 1).each((_, row) =>
        $(row)
          .children()
          .slice(Math.min(c1, c2), Math.max(c1, c2) + 1)
          .filter((__, c) => $(c).find(toggleSelector).length)
          .addClass("drag-selecting"),
      );
    });

  $(document).on("mouseup", () => {
    if (!drag) return;
    const $targets = $(".drag-selecting").find(".checkin-toggle");
    $(".drag-selecting").removeClass("drag-selecting");
    if ($targets.length) submitCheckIn($targets, drag.toggle);
    drag = null;
  });

  $(document)
    .on("click", ".checkin-toggle, .checkin-label", (e) => e.preventDefault())
    .on("click", ".bulk-toggle", (e) => {
      const targets = getBulkTargets(e.currentTarget);
      const shouldCheckIn = $(e.currentTarget).data("action") === "check_in";
      if (targets.length) submitCheckIn(targets, shouldCheckIn);
    });
});
