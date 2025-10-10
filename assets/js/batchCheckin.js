import $ from "jquery";
import "../css/batchCheckin.scss";

const debounce = (fn, ms) => {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
};
const setLabel = ($cb, checked) =>
  $cb.next("label").text(`Checked ${checked ? "In" : "Out"}`);
const applyCheckboxState = ($boxes, value) =>
  $boxes.each((i, cb) => {
    const state = Array.isArray(value) ? value[i] : value;
    const $cb = $(cb).prop("checked", state);
    setLabel($cb, state);
  });
const getTargets = $toggle => {
  const type = $toggle.data("entityType");
  const scope = $toggle.data("toggleScope");
  const $pane = $toggle.closest(".tab-pane");
  const sel = `.checkin-toggle[data-entity-type="${type}"]`;

  if (scope === "row") {
    const $row = $toggle.closest("tr");
    return $row.is(":visible") ? $row.find(sel) : $();
  }

  const round = $toggle.data("round");
  return $pane.find(sel).filter((_, el) => {
    const $el = $(el);
    if (type !== "team" && round != null && $el.data("roundNumber") !== round)
      return false;
    const $row = $el.closest(".searchable");
    return !$row.length || $row.is(":visible");
  });
};
const refreshBulk = paneSelector => {
  const $pane = paneSelector
    ? $(paneSelector)
    : $(".tab-pane").filter(".active");
  const $target = $pane.length ? $pane : $(".tab-pane").first();

  requestAnimationFrame(() => {
    $target.find(".bulk-toggle").each((_, toggle) => {
      const $targets = getTargets($(toggle));
      $(toggle).prop(
        "checked",
        $targets.length && $targets.toArray().every(cb => cb.checked)
      );
    });
  });
};
const submit = ({ $targets, type, checked, $trigger }) => {
  if (!$targets.length) return;

  const ids = new Set();
  const rounds = new Set();

  $targets.each((_, el) => {
    const $el = $(el);
    const id = $el.data("entityId");
    if (id) ids.add(id);
    if (type !== "team") {
      const r = $el.data("roundNumber");
      if (r != null) rounds.add(r);
    }
  });

  if (!ids.size) {
    if ($trigger) $trigger.prop("checked", false);
    return;
  }

  const fd = new FormData();
  const token = $("[name=csrfmiddlewaretoken]").val();
  fd.append("csrfmiddlewaretoken", token);
  fd.append("entity_type", type);
  fd.append("action", checked ? "check_in" : "check_out");
  ids.forEach(id => fd.append(`${type}_ids`, id));
  if (type !== "team") rounds.forEach(r => fd.append("round_numbers", r));

  const prev = $targets.map((_, cb) => cb.checked).get();

  applyCheckboxState($targets, checked);
  refreshBulk();
  if ($trigger) $trigger.prop("disabled", true);

  $.ajax({
    url: "/bulk_check_in/",
    method: "POST",
    data: fd,
    processData: false,
    contentType: false,
    beforeSend: xhr => xhr.setRequestHeader("X-CSRFToken", token)
  })
    .done(() => refreshBulk())
    .fail(() => {
      alert("Bulk operation failed");
      applyCheckboxState($targets, prev);
      refreshBulk();
      if ($trigger) $trigger.prop("checked", !checked);
    })
    .always(() => {
      if ($trigger) $trigger.prop("disabled", false);
    });
};
const drag = {
  active: false,
  start: null,
  end: null,
  toggle: null,
  $cells: $()
};
const getCoords = $cell => {
  const $row = $cell.closest("tr");
  return {
    row: $row
      .closest("table")
      .find("tr")
      .index($row),
    col: $row.find("td, th").index($cell)
  };
};
const getRect = ($table, r1, r2, c1, c2) => {
  const $res = $();
  $table
    .find("tr")
    .slice(r1, r2 + 1)
    .each((__, row) =>
      $(row)
        .find("td, th")
        .slice(c1, c2 + 1)
        .each(
          (_, cell) => $(cell).find(".checkin-toggle").length && $res.push(cell)
        )
    );
  return $res;
};
const startDrag = e => {
  const $cell = $(e.target).closest("td, th");
  const $cb = $cell.find(".checkin-toggle");
  if (!$cell.length || !$cb.length || $cb.hasClass("bulk-toggle")) return;

  const pos = getCoords($cell);
  drag.active = true;
  drag.start = pos;
  drag.end = pos;
  drag.toggle = !$cb.prop("checked");
  $cell.closest(".tab-pane").addClass("dragging");
  drag.$cells = getRect(
    $cell.closest("table"),
    pos.row,
    pos.row,
    pos.col,
    pos.col
  ).addClass("drag-selecting");
};
const moveDrag = e => {
  if (!drag.active) return;
  const $cell = $(e.target).closest("td, th");
  if (!$cell.length) return;

  const pos = getCoords($cell);
  if (drag.end && drag.end.row === pos.row && drag.end.col === pos.col) return;

  drag.end = pos;
  $(".drag-selecting").removeClass("drag-selecting");
  drag.$cells = getRect(
    $cell.closest("table"),
    Math.min(drag.start.row, pos.row),
    Math.max(drag.start.row, pos.row),
    Math.min(drag.start.col, pos.col),
    Math.max(drag.start.col, pos.col)
  ).addClass("drag-selecting");
};
const endDrag = () => {
  if (!drag.active) return;

  const $targets = drag.$cells
    .map((_, cell) => $(cell).find(".checkin-toggle")[0])
    .filter((_, cb) => cb);
  $(".drag-selecting").removeClass("drag-selecting");
  $(".tab-pane").removeClass("dragging");
  drag.active = false;
  drag.$cells = $();

  if ($targets.length)
    submit({
      $targets,
      type: $targets.first().data("entityType"),
      checked: drag.toggle
    });
};

$(() => {
  const cellSel = ".tab-pane table td, .tab-pane table th";
  const labelSel = ".tab-pane table .checkin-label";
  $(document)
    .on("click", ".checkin-toggle:not(.bulk-toggle)", e => e.preventDefault())
    .on("click", labelSel, e => e.preventDefault())
    .on("mousedown", cellSel, startDrag)
    .on("mousemove", cellSel, moveDrag)
    .on("mouseup", endDrag)
    .on("mouseleave", ".tab-pane table", endDrag);

  $(".bulk-toggle").on("change", e => {
    const $toggle = $(e.currentTarget);
    const $targets = getTargets($toggle);
    if (!$targets.length) {
      $toggle.prop("checked", false);
      return;
    }
    submit({
      $targets,
      type: $toggle.data("entityType"),
      checked: $toggle.prop("checked"),
      $trigger: $toggle
    });
  });

  $('a[data-toggle="tab"]').on("click", e =>
    setTimeout(refreshBulk, 250, $(e.currentTarget).attr("href"))
  );
  $("#quick-search").on("input keyup", debounce(() => refreshBulk(), 120));
  refreshBulk();
});
