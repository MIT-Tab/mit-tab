

function roomCheckInOrOut(target, isCheckIn) {
  const $target = $(target);
  $target.prop("disabled", true);

  const roomId = $target.data("room-id");
  const roundNumber = $target.data("round-number");

  const $label = $(`label[for=${$target.attr("id")}]`);
  $label.text(isCheckIn ? "Checked In" : "Checked Out");

  const url = `/room/${roomId}/check_ins/round/${roundNumber}/`;
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
      window.alert("An error occured. Refresh and try again");
    }
  });
}

function roomCheckinInit() {
  $(".room-checkin-toggle").click(e => {
    roomCheckInOrOut(e.target, $(e.target).prop("checked"));
  });
}

export default roomCheckinInit;
