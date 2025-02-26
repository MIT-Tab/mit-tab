import $ from "jquery";
import quickSearchInit from "./quickSearch";

function checkInOrOut(target, isCheckIn, type) {
  const $target = $(target);
  $target.prop("disabled", true);

  const id = $target.data(`${type}-id`);
  const roundNumber = $target.data("round-number");
  const $label = $(`label[for=${$target.attr("id")}]`);
  $label.text(isCheckIn ? "Checked In" : "Checked Out");

  let url = `/${type}/${id}/check_ins/`;
  if (roundNumber !== undefined) {
    url += `round/${roundNumber}/`;
  }

  const method = isCheckIn ? "POST" : "DELETE";

  $.ajax({
    url,
    beforeSend(xhr) {
      xhr.setRequestHeader(
        "X-CSRFToken",
        $("[name=csrfmiddlewaretoken]").val()
      );
    },
    method,
    success() {
      $target.prop("disabled", false);
    },
    error() {
      $target.prop("disabled", false);
      $target.prop("checked", !isCheckIn);
      $label.text(isCheckIn ? "Checked Out" : "Checked In");
      window.alert("An error occurred. Refresh and try again");
    }
  });
}

function checkinInit() {
  $(".judge-checkin-toggle").click(e => {
    checkInOrOut(e.target, $(e.target).prop("checked"), "judge");
  });
  $(".team-checkin-toggle").click(e => {
    checkInOrOut(e.target, $(e.target).prop("checked"), "team");
  });
  $(".room-checkin-toggle").click(e => {
    checkInOrOut(e.target, $(e.target).prop("checked"), "room");
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const categorySelect = document.getElementById("categorySelect");

  const checkinContainers = {
    team: document.getElementById("teamCheckin"),
    judge: document.getElementById("judgeCheckin"),
    room: document.getElementById("roomCheckin")
  };

  const searchInputs = {
    team: document.getElementById("team-search"),
    judge: document.getElementById("judge-search"),
    room: document.getElementById("room-search")
  };

  function updateCheckinVisibility(selected) {
    Object.values(checkinContainers).forEach(el => el.classList.add("hidden"));
    checkinContainers[selected].classList.remove("hidden");

    quickSearchInit(searchInputs[selected]);
  }

  const savedSelection = localStorage.getItem("selectedCategory") || "team";
  categorySelect.value = savedSelection;
  updateCheckinVisibility(savedSelection);

  categorySelect.addEventListener("change", e => {
    localStorage.setItem("selectedCategory", e.target.value);
    updateCheckinVisibility(e.target.value);
  });

  quickSearchInit(searchInputs[savedSelection]);
  checkinInit();
});

export default checkinInit;
