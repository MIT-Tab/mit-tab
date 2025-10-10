import $ from "jquery";
import "../css/batchCheckin.scss";

const SEL = {
  pane: ".tab-pane",
  check: ".checkin-toggle",
  bulk: ".bulk-toggle",
  cell: "td, th",
  csrf: "[name=csrfmiddlewaretoken]"
};

const debounce = (fn, ms) => {
  let timeout;
  return (...args) => {
    clearTimeout(timeout);
    timeout = setTimeout(() => fn(...args), ms);
  };
};

const parseRound = val => {
  const n = parseInt(val, 10);
  return Number.isNaN(n) ? null : n;
};

const collectTargets = $toggle => {
  const type = $toggle.data("entityType");
  const scope = $toggle.data("toggleScope");
  const $pane = $toggle.closest(SEL.pane);
  const selector = `${SEL.check}[data-entity-type="${type}"]`;

  if (scope === "row") {
    const $row = $toggle.closest("tr");
    return $row.is(":visible") ? $row.find(selector) : $();
  }

  let $targets = $pane.find(selector);

  if (type !== "team") {
    const round = parseRound($toggle.data("round"));
    if (round !== null) {
      $targets = $targets.filter(
        (_, el) => parseRound(el.dataset.roundNumber) === round
      );
    }
  }

  return $targets.filter((_, el) => {
    const $searchable = $(el).closest(".searchable");
    return !$searchable.length || $searchable.css("display") !== "none";
  });
};

const scheduleRefresh = paneSelector => {
  const $pane = paneSelector ? $(paneSelector) : $(SEL.pane).filter(".active");
  const refresh = () => {
    ($pane.length ? $pane : $(SEL.pane).first())
      .find(SEL.bulk)
      .each((_, toggle) => {
        const $targets = collectTargets($(toggle));
        $(toggle).prop(
          "checked",
          $targets.length > 0 && $targets.toArray().every(el => el.checked)
        );
      });
  };

  requestAnimationFrame(() => {
    refresh();
    requestAnimationFrame(refresh);
    setTimeout(refresh, 200);
  });
};

const sendUpdate = ({ $targets, type, checked, $trigger }) => {
  if (!$targets.length) return;

  const ids = new Set();
  const rounds = new Set();

  $targets.each((_, el) => {
    const id = $(el).data("entityId");
    if (id) ids.add(id);
    if (type !== "team") {
      const round = parseRound($(el).data("roundNumber"));
      if (round !== null) rounds.add(round);
    }
  });

  if (!ids.size) {
    if ($trigger) $trigger.prop("checked", false);
    return;
  }

  const formData = new FormData();
  formData.append("csrfmiddlewaretoken", $(SEL.csrf).val());
  formData.append("entity_type", type);
  formData.append("action", checked ? "check_in" : "check_out");
  [...ids].forEach(id => formData.append(`${type}_ids`, id));
  if (type !== "team")
    [...rounds].forEach(r => formData.append("round_numbers", r));

  const prevStates = $targets.map((_, el) => el.checked).get();

  $targets.each((_, el) => {
    $(el)
      .prop("checked", checked)
      .next("label")
      .text(`Checked ${checked ? "In" : "Out"}`);
  });
  scheduleRefresh();
  if ($trigger) $trigger.prop("disabled", true);

  $.ajax({
    url: "/bulk_check_in/",
    method: "POST",
    data: formData,
    processData: false,
    contentType: false,
    beforeSend: xhr => xhr.setRequestHeader("X-CSRFToken", $(SEL.csrf).val())
  })
    .done(() => {
      scheduleRefresh();
    })
    .fail(() => {
      alert("Bulk operation failed");
      $targets.each((i, el) => {
        $(el)
          .prop("checked", prevStates[i])
          .next("label")
          .text(`Checked ${prevStates[i] ? "In" : "Out"}`);
      });
      scheduleRefresh();
      if ($trigger) $trigger.prop("checked", !checked);
    })
    .always(() => {
      if ($trigger) $trigger.prop("disabled", false);
    });
};

const drag = {
  active: false,
  start: null,
  startCell: null,
  end: null,
  initialState: null,
  $cells: $()
};

const getCellCoords = $cell => {
  const $row = $cell.closest("tr");
  return {
    row: $row
      .closest("table")
      .find("tr")
      .index($row),
    col: $row.find(SEL.cell).index($cell)
  };
};

const collectRect = ($table, bounds) => {
  const $cells = $();
  $table
    .find("tr")
    .slice(bounds.minRow, bounds.maxRow + 1)
    .each((unusedIndex, row) => {
      $(row)
        .find(SEL.cell)
        .slice(bounds.minCol, bounds.maxCol + 1)
        .each((unusedIdx, cell) => {
          if ($(cell).find(SEL.check).length) $cells.push(cell);
        });
    });
  return $cells;
};

const resetDrag = () => {
  $(".drag-selecting").removeClass("drag-selecting");
  drag.active = null;
  drag.start = null;
  drag.startCell = null;
  drag.end = null;
  drag.$cells = $();
};

const onDragStart = e => {
  const $cell = $(e.target).closest(SEL.cell);
  const $checkbox = $cell.find(SEL.check);
  if (!$cell.length || !$checkbox.length || $checkbox.hasClass("bulk-toggle"))
    return;

  drag.active = true;
  const coords = getCellCoords($cell);
  drag.start = coords;
  drag.end = coords;
  const [firstCell] = $cell;
  drag.startCell = firstCell;
  drag.initialState = $checkbox.prop("checked");
  drag.$cells = collectRect($cell.closest("table"), {
    minRow: drag.start.row,
    maxRow: drag.start.row,
    minCol: drag.start.col,
    maxCol: drag.start.col
  });
  drag.$cells.addClass("drag-selecting");
};

const onDragMove = e => {
  if (!drag.active) return;

  const $cell = $(e.target).closest(SEL.cell);
  if (!$cell.length) return;

  const coords = getCellCoords($cell);
  if (drag.end && drag.end.row === coords.row && drag.end.col === coords.col)
    return;

  drag.end = coords;
  $(".drag-selecting").removeClass("drag-selecting");
  drag.$cells = collectRect($cell.closest("table"), {
    minRow: Math.min(drag.start.row, drag.end.row),
    maxRow: Math.max(drag.start.row, drag.end.row),
    minCol: Math.min(drag.start.col, drag.end.col),
    maxCol: Math.max(drag.start.col, drag.end.col)
  });
  drag.$cells.addClass("drag-selecting");
};

const onDragEnd = e => {
  if (!drag.active) return undefined;

  const isSameCell = $(e.target).closest(SEL.cell)[0] === drag.startCell;
  const { $cells } = drag;
  const checked = !drag.initialState;

  resetDrag();

  // Single cell click - toggle that one checkbox
  if (isSameCell) {
    const $cell = $(e.target).closest(SEL.cell);
    const $cb = $cell.find(SEL.check);
    if ($cb.length && !$(e.target).is('input[type="checkbox"], label')) {
      $cb.prop("checked", checked);
      sendUpdate({ $targets: $cb, type: $cb.data("entityType"), checked });
    }
    return undefined;
  }

  // Multi-cell drag - toggle all selected checkboxes
  const $targets = $();
  $cells.each((_, cell) => {
    const $cb = $(cell).find(SEL.check);
    if ($cb.length) $targets.push($cb[0]);
  });

  if ($targets.length) {
    const $first = $targets.first();
    sendUpdate({ $targets, type: $first.data("entityType"), checked });
  }
  return undefined;
};

$(() => {
  $(SEL.check).on("change", e => {
    const $cb = $(e.currentTarget);
    sendUpdate({
      $targets: $cb,
      type: $cb.data("entityType"),
      checked: $cb.prop("checked")
    });
  });

  $(SEL.bulk).on("change", e => {
    const $toggle = $(e.currentTarget);
    const $targets = collectTargets($toggle);
    if (!$targets.length) {
      $toggle.prop("checked", false);
      return;
    }
    sendUpdate({
      $targets,
      type: $toggle.data("entityType"),
      checked: $toggle.prop("checked"),
      $trigger: $toggle
    });
  });

  $('a[data-toggle="tab"]').on("click", e =>
    setTimeout(() => scheduleRefresh($(e.currentTarget).attr("href")), 250)
  );
  $("#quick-search").on("input keyup", debounce(() => scheduleRefresh(), 120));

  $(document).on("mousedown", `${SEL.pane} table ${SEL.cell}`, onDragStart);
  $(document).on("mousemove", `${SEL.pane} table ${SEL.cell}`, onDragMove);
  $(document).on("mouseup", onDragEnd);
  $(document).on("mouseleave", `${SEL.pane} table`, e => {
    if (drag.active) {
      drag.startCell = null;
      onDragEnd(e);
    }
  });

  scheduleRefresh();
});
