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
    Object.keys(filters).forEach(key => {
      const rowValue = $(row).data(key);
      if (rowValue !== filters[key]) {
        show = false;
      }
    });
    $(row).toggle(show);
  });
}

function filtersInit() {
  $(".filter").change(applyFilters);
  applyFilters();
  $(".dropdown-filter").change(applyDropdownFilters);
}

export default filtersInit;
