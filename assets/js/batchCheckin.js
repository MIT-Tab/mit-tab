import $ from "jquery";
import "../css/batchCheckin.scss";

const submitCheckIn = (checkboxes, checked) => {
  const $boxes = $(checkboxes);
  if (!$boxes.length) return;

  const entityType = $boxes
    .first()
    .closest("[data-entity-type]")
    .data("entityType");

  $.post("/bulk_check_in/", {
    csrfmiddlewaretoken: $("[name=csrfmiddlewaretoken]").val(),
    entity_type: entityType,
    action: checked ? "check_in" : "check_out",
    entity_ids: [...new Set($boxes.map((_, el) => $(el).data("id")).get())],
    rounds: [
      ...new Set($boxes.map((_, el) => $(el).data("round")).get())
    ].filter(r => r != null)
  })
    .done(() =>
      $boxes.each((_, cb) =>
        $(cb)
          .prop("checked", checked)
          .next("label")
          .text(`Checked ${checked ? "In" : "Out"}`)
      )
    )
    .fail(() => alert("Operation failed"));
};

const getBulkTargets = btn => {
  const { scope, round } = $(btn).data();
  const entityType = $(btn)
    .closest("[data-entity-type]")
    .data("entityType");
  const base = `.checkin-toggle`;
  const sel = `${base}:not(.bulk-toggle)`;

  if (scope === "row")
    return $(btn)
      .closest("tr:visible")
      .find(sel);

  return $(btn)
    .closest(".tab-pane")
    .find(sel)
    .filter((_, el) => {
      const $el = $(el);
      return (
        (entityType === "team" ||
          round == null ||
          $el.data("round") === round) &&
        $el.closest("tr").is(":visible")
      );
    });
};

$(() => {
  let drag = null;

  $(".tab-pane table")
    .on("mousedown", "td, th", e => {
      const $cell = $(e.currentTarget);
      if (!$cell.find(".checkin-toggle:not(.bulk-toggle)").length) return;

      drag = {
        start: $cell,
        toggle: !$cell.find(".checkin-toggle").prop("checked")
      };
      $cell.addClass("drag-selecting");
      e.preventDefault();
    })
    .on("mouseenter", "td, th", e => {
      if (!drag) return;

      const $end = $(e.currentTarget);
      const $rows = drag.start.closest("table").find("tr");
      const [r1, c1] = [
        $rows.index(drag.start.parent()),
        drag.start
          .parent()
          .children()
          .index(drag.start)
      ];
      const [r2, c2] = [
        $rows.index($end.parent()),
        $end
          .parent()
          .children()
          .index($end)
      ];

      $(".drag-selecting").removeClass("drag-selecting");
      $rows.slice(Math.min(r1, r2), Math.max(r1, r2) + 1).each((_, row) =>
        $(row)
          .children()
          .slice(Math.min(c1, c2), Math.max(c1, c2) + 1)
          .filter(
            (__, c) => $(c).find(".checkin-toggle:not(.bulk-toggle)").length
          )
          .addClass("drag-selecting")
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
    .on("click", ".checkin-toggle:not(.bulk-toggle), .checkin-label", e =>
      e.preventDefault()
    )
    .on("click", ".bulk-toggle", e => {
      const targets = getBulkTargets(e.currentTarget);
      if (targets.length)
        submitCheckIn(
          targets,
          $(e.currentTarget).data("action") === "check_in"
        );
    });
});
