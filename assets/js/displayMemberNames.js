import $ from "jquery";

function changeDisplayNames() {
  const checkBox = document.getElementById("name_display_toggle");
  if (checkBox) {
    document.body.classList.toggle("show-team-names", !checkBox.checked);
    window.dispatchEvent(new Event("memberNamesToggled"));
  }
}

$(document).ready(() => $("#name_display_toggle").click(changeDisplayNames));
