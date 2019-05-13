import $ from "jquery";

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
  $(searchElem).keyup(() => {
    if ($(this).val()) {
      filter($(this).val());
    } else {
      $(".searchable").show();
    }
  });
}

export default quickSearchInit;
