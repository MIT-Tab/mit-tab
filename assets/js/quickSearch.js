

$.expr[":"].Contains = (a, i, m) => {
  return (
    $(a)
      .text()
      .toUpperCase()
      .indexOf(m[3].toUpperCase()) >= 0
  );
};

function filter(matchingText) {
  $(`.searchable:not(:Contains(${matchingText}))`).hide();
  $(`.searchable:Contains(${matchingText})`).show();
}

function quickSearchInit(elem) {
  let searchElem = elem;
  if (!searchElem) {
    searchElem = $("#quick-search");
  }
  $(searchElem).keyup(e => {
    if ($(e.target).val()) {
      filter($(e.target).val());
    } else {
      $(".searchable").show();
    }
  });
}

export default quickSearchInit;
