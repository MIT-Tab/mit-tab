
import "select2";
import "select2/dist/css/select2.css";
import "@ttskch/select2-bootstrap4-theme/dist/select2-bootstrap4.min.css";

function multiselectInit($elem) {
  let $select = $elem;
  if (!$select) {
    $select = $("#data-entry-form select[multiple]");
  }
  $select.select2({ theme: "bootstrap4" });
}


$(function() {
  multiselectInit();
});