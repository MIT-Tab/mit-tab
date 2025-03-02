import $ from "jquery";

function changeDisplayNames() {
  const checkBox = document.getElementById("name_display_toggle");

  document.body.classList.toggle("show-team-names", !checkBox.checked);
}

$(document).ready(() => {
  $("#name_display_toggle").click(changeDisplayNames);
});
