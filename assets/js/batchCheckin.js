import $ from "jquery";
import "../css/batchCheckin.scss";

const ENTITY_TYPES = ["team", "judge", "room"];
const getType = el =>
  ENTITY_TYPES.find(type => el.className.includes(type)) || "room";

const getCheckinSelector = () =>
  ENTITY_TYPES.map(type => `.${type}-checkin-toggle`).join(", ");

const getBulkColSelector = () =>
  ENTITY_TYPES.map(type => `.bulk-${type}-col-toggle`).join(", ");

const getBulkRowSelector = () =>
  ENTITY_TYPES.filter(type => type !== "team")
    .map(type => `.bulk-${type}-row-toggle`)
    .join(", ");

const ajax = (url, method, data = null) =>
  $.ajax({
    url,
    method,
    data,
    processData: false,
    contentType: false,
    beforeSend: xhr =>
      xhr.setRequestHeader("X-CSRFToken", $("[name=csrfmiddlewaretoken]").val())
  });

const getStats = (type, round = null, entityId = null) => {
  const baseSelector = `.${type}-checkin-toggle`;
  const entityFilter = entityId ? `[data-${type}-id="${entityId}"]` : "";
  const roundFilter =
    type === "team" || !round ? "" : `[data-round-number="${round}"]`;

  const selector = entityId
    ? `${baseSelector}${entityFilter}`
    : `.searchable:visible ${baseSelector}${roundFilter}`;

  const $checkboxes = $(selector);
  const checked = $checkboxes.filter(":checked").length;
  return { checked, total: $checkboxes.length };
};

const updateBulkToggles = () => {
  const selectors = ENTITY_TYPES.flatMap(type => [
    `.bulk-${type}-col-toggle`,
    ...(type !== "team" ? [`.bulk-${type}-row-toggle`] : [])
  ]).join(", ");

  $(selectors).each((_, toggle) => {
    const $toggle = $(toggle);
    const type = getType(toggle);
    const isRow = toggle.className.includes("row");
    const round = $toggle.data("round");
    const entityId = isRow ? $toggle.data(`${type}-id`) : null;

    const { checked, total } = getStats(type, round, entityId);
    $toggle.prop("checked", checked === total && total > 0);
  });
};

const bulkUpdate = (ids, type, roundNumbers, checkIn, $bulkToggle = null) => {
  if (!ids.length) return;

  let loadingTimeout;
  if ($bulkToggle) {
    $bulkToggle.prop("disabled", true);
    const $label = $bulkToggle.next("label");
    $label.data("original-text", $label.text());
    loadingTimeout = setTimeout(() => $label.text("Loading..."), 300);
  }

  const data = new FormData();
  data.append("csrfmiddlewaretoken", $("[name=csrfmiddlewaretoken]").val());
  data.append("action", checkIn ? "check_in" : "check_out");
  ids.forEach(id => data.append(`${type}_ids`, id));
  if (roundNumbers)
    roundNumbers.forEach(rn => data.append("round_numbers", rn));

  ajax(`/${type}/bulk_check_in/`, "POST", data)
    .done(() => {
      ids.forEach(id =>
        (roundNumbers || [null]).forEach(round => {
          const selector = `.${type}-checkin-toggle[data-${type}-id="${id}"]${
            type !== "team" ? `[data-round-number="${round}"]` : ""
          }`;
          $(selector)
            .prop("checked", checkIn)
            .next("label")
            .text(`Checked ${checkIn ? "In" : "Out"}`);
        })
      );
      updateBulkToggles();
    })
    .fail(() => {
      alert("Bulk operation failed");
      if ($bulkToggle) $bulkToggle.prop("checked", !checkIn);
    })
    .always(() => {
      if ($bulkToggle) {
        clearTimeout(loadingTimeout);
        $bulkToggle.prop("disabled", false);
        $bulkToggle
          .next("label")
          .text($bulkToggle.next("label").data("original-text"));
      }
    });
};

const checkInOrOut = (target, isCheckIn, type) => {
  const $target = $(target);
  bulkUpdate(
    [$target.data(`${type}-id`)],
    type,
    type === "team" ? null : [$target.data("round-number")],
    isCheckIn
  );
};

const getRoundNumber = text => {
  if (text.includes("Round")) return parseInt(text.replace("Round ", ""), 10);
  if (text.includes("Outrounds")) return 0;
  return null;
};

const handleBulkToggle = (e, type, isRow = false) => {
  const $toggle = $(e.target);

  const ids = isRow
    ? [$toggle.data(`${type}-id`)]
    : $(`.searchable:visible .${type}-checkin-toggle`)
        .map((_, el) => $(el).data(`${type}-id`))
        .get();

  let rounds = null;
  if (type !== "team") {
    if (isRow) {
      rounds = $("th")
        .map((_, th) => getRoundNumber($(th).text()))
        .get()
        .filter(r => r !== null);
    } else {
      rounds = [$toggle.data("round")];
    }
  }

  bulkUpdate(ids, type, rounds, $toggle.prop("checked"), $toggle);
};

let updateTimeout;
const debouncedUpdateBulkToggles = () => {
  clearTimeout(updateTimeout);
  updateTimeout = setTimeout(updateBulkToggles, 100);
};

$(() => {
  $("#quick-search").on("input keyup", debouncedUpdateBulkToggles);

  $(getCheckinSelector()).on("click", e => {
    checkInOrOut(e.target, e.target.checked, getType(e.target));
    debouncedUpdateBulkToggles();
  });

  $(getBulkColSelector()).on("click", e =>
    handleBulkToggle(e, getType(e.target))
  );

  $(getBulkRowSelector()).on("click", e =>
    handleBulkToggle(e, getType(e.target), true)
  );

  setTimeout(updateBulkToggles, 100);
});
