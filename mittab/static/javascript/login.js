$(document).ready(() => { 
  $("#teams-lookup").select2();
  
  $('.select2-selection').on('keyup', ((event) => {
    if (event.keyCode === 13) {
        $("#team_btn").click();
    }
  }));

  $('#team_btn').click(() => {
    val = document.getElementById('teams-lookup').value;
    window.location.href = '/public_status/' + val + '/';
  });
});