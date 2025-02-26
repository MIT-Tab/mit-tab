import $ from "jquery";

document.addEventListener("DOMContentLoaded", function () {
  const categorySelect = document.getElementById("categorySelect");
  const contentContainer = document.getElementById("contentContainer");

  const loadContent = (value) => {
    fetch(`${value}/`)
      .then(response => response.text())
      .then(html => {
        contentContainer.innerHTML = html;
      })
      .then(() => checkinInit()) 
      .catch(error => console.error("Error loading content:", error));
  };

  const savedSelection = localStorage.getItem("selectedCategory") || "team";
  categorySelect.value = savedSelection;
  loadContent(savedSelection);

  categorySelect.addEventListener("change", function () {
    localStorage.setItem("selectedCategory", this.value);
    loadContent(this.value);
  });
});

function checkInOrOut(target, isCheckIn, type) {
  const $target = $(target);
  
  if ($target.prop("disabled")) return; 

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
    method,
    beforeSend(xhr) {
      xhr.setRequestHeader(
        "X-CSRFToken",
        $("[name=csrfmiddlewaretoken]").val()
      );
    },
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
  $(document).off("click", ".judge-checkin-toggle, .team-checkin-toggle, .room-checkin-toggle");

  $(document).on("click", ".judge-checkin-toggle", function () {
    checkInOrOut(this, $(this).prop("checked"), "judge");
  });

  $(document).on("click", ".team-checkin-toggle", function () {
    checkInOrOut(this, $(this).prop("checked"), "team");
  });

  $(document).on("click", ".room-checkin-toggle", function () {
    checkInOrOut(this, $(this).prop("checked"), "room");
  });
  
}

export default checkinInit;
