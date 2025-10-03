import $ from "jquery";

function filterOnFlags(flags) {
  $("li.filterable").each((index, element) => {
    let show = 1;
    const flagGroups = Object.keys(flags);
    flagGroups.forEach(flagGroup => {
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

function filtersInit() {
  $(".filter").change(applyFilters);
  $("#toggle-judge-display").click(toggleJudgeDisplay);
  applyFilters();
}

export default filtersInit;
