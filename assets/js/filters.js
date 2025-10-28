import $ from "jquery";

function filterOnFlags(flags) {
  $("li.filterable").each((index, element) => {
    let show = 1;
    const flagGroups = Object.keys(flags);
    flagGroups.forEach((flagGroup) => {
      show &= ($(element).data("filters") & flags[flagGroup]) > 0;
    });

    $(element).toggle(!!show);
  });
}

function applyFilters() {
  $("li.data-list").show();
  const filterGroups = {};
  $(".filter").each((index, value) => {
    if (value.checked) {
      filterGroups[$(value).data("filter-group")] |= $(value).data("filter");
    }
  });
  filterOnFlags(filterGroups);
}

function toggleJudgeDisplay() {
  const button = $("#toggle-judge-display");
  const details = $(".item-detail");

  if (button.text() === "Show Rank") {
    details.each((index, element) => {
      $(element).text($(element).data("rank"));
    });
    button.text("Show Code");
  } else {
    details.each((index, element) => {
      $(element).text($(element).data("code"));
    });
    button.text("Show Rank");
  }
}

function applyDropdownFilters() {
  const filters = {};
  $(".dropdown-filter").each((index, elem) => {
    const value = $(elem).val();
    const filterKey = $(elem).data("filter-key");
    if (value && filterKey) {
      filters[filterKey] = value;
    }
  });

  $("tr.filterable-row").each((index, row) => {
    let show = true;
    Object.keys(filters).forEach((key) => {
      const rowValue = $(row).data(key);
      if (String(rowValue) !== String(filters[key])) {
        show = false;
      }
    });
    $(row).toggle(show);
  });
}

function filtersInit() {
  $(".filter").change(applyFilters);
  $("#toggle-judge-display").click(toggleJudgeDisplay);
  applyFilters();
  $(".dropdown-filter").change(applyDropdownFilters);
}

export default filtersInit;
