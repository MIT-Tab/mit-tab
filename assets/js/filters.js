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

function filtersInit() {
  $(".filter").change(applyFilters);
  applyFilters();
}

export default filtersInit;
