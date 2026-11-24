import $ from "jquery";
import "select2";
import "select2/dist/css/select2.css";
import "../css/ui/select2.scss";

function multiselectInit($elem) {
  let $select = $elem;
  if (!$select) {
    $select = $(
      "#data-entry-form select[multiple], select[multiple].js-multiselect",
    );
  }
  $select.select2();
}

export default multiselectInit;
