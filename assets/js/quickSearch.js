import $ from 'jquery'

$.expr[':'].Contains = function(a,i,m){
    return $(a).text().toUpperCase().indexOf(m[3].toUpperCase())>=0;
};

function filter(matching_text) {
  $('.searchable:not(:Contains(' + matching_text+ '))').hide(); 
  $('.searchable:Contains(' + matching_text + ')').show();
};

function quickSearchInit(elem) {
  if (!elem) {
    elem = $('#quick-seach')
  }
  $(elem).keyup(function() {
    console.log('hello')
    if ($(this).val()) {
      filter($(this).val());
    }
    else {
      $(".searchable").show();
    }
  });
}

export default quickSearchInit;
