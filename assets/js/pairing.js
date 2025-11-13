import $ from "jquery";

import quickSearchInit from "./quickSearch";

function populateTabCards() {
  const roundNumber = $("#round-number").data("round-number");
  if (!roundNumber || !$(".tabcard").length) {
    return;
  }
  $.ajax({
    url: `/round/${roundNumber}/stats`,
    success(result) {
      Object.entries(result).forEach(([teamId, stats]) => {
        const tabCardElement = $(`.tabcard[team-id=${teamId}]`);
        const text = [
          stats.wins,
          stats.total_speaks.toFixed(2),
          stats.govs,
          stats.opps,
          stats.seed,
        ].join(" / ");
        tabCardElement.attr("title", "Wins / Speaks / Govs / Opps / Seed");
        tabCardElement.attr("href", `/team/card/${teamId}`);
        tabCardElement.text(`${text}`);
      });
    },
  });
}

function assignTeam(e) {
  e.preventDefault();
  const teamId = $(e.target).attr("team-id");
  const roundId = $(e.target).attr("round-id");
  const position = $(e.target).attr("position");
  const url = `/pairings/assign_team/${roundId}/${position}/${teamId}`;
  const alertMsg = `
    An error occured.
    Refresh the page and try to fix any inconsistencies you may notice.
  `;

  $.ajax({
    url,
    success(result) {
      if (result.success) {
        const $container = $(`.row[round-id=${roundId}] .${position}-team`);
        $container.find(".team-assign-button").attr("team-id", result.team.id);
        $container.find(".team-link").text(result.team.name);
        $container.find(".team-link").attr("href", `/team/${result.team.id}`);
        $container.find(".tabcard").attr("team-id", result.team.id);

        populateTabCards();
      } else {
        window.alert(alertMsg);
      }
    },
    failure() {
      window.alert(alertMsg);
    },
  });
}

function updateTeamSection(roundId, position, team) {
  const $container = $(`.row[round-id=${roundId}] .${position}-team`);
  $container.find(".team-assign-button").attr("team-id", team.id);
  $container.find(".team-link").text(team.name);
  $container.find(".team-link").attr("href", `/team/${team.id}`);
  $container
    .find(".tabcard, .outround-tabcard")
    .attr("team-id", team.id)
    .text("Loading results...");
}

function handleSwitchSides(event) {
  event.preventDefault();
  const $target = $(event.currentTarget);
  const roundId = $target.attr("round-id");
  const isOutround = Boolean($target.data("outround"));
  const baseUrl = isOutround ? "/outround" : "/round";
  const url = `${baseUrl}/${roundId}/switch_sides/`;
  const alertMsg = "Unable to switch sides. Refresh and try again.";

  $.ajax({
    url,
    success(result) {
      if (result.success) {
        updateTeamSection(roundId, "gov", result.gov_team);
        updateTeamSection(roundId, "opp", result.opp_team);
        if (isOutround) {
          $(document).trigger("outround:switch", {
            roundId,
            govTeam: result.gov_team,
            oppTeam: result.opp_team,
          });
        } else {
          populateTabCards();
        }
      } else {
        window.alert(alertMsg);
      }
    },
    error() {
      window.alert(alertMsg);
    },
  });
}

function assignRoom(e) {
  e.preventDefault();
  const $parent = $(this).parent().parent();
  const roundId = $(e.target).attr("round-id");
  const roomId = $(e.target).attr("room-id");
  const curRoomId = $(e.target).attr("current-room-id");
  const outround = $parent.attr("outround") === "true";
  const baseUrl = outround ? "/outround" : "/round";
  const url = `${baseUrl}/${roundId}/assign_room/${roomId}/`;

  let $buttonWrapper;
  if (curRoomId) {
    $buttonWrapper = $(`span[round-id=${roundId}][room-id=${curRoomId}]`);
  }
  const $button = $buttonWrapper.find(".btn-sm");
  $button.addClass("disabled");

  $.ajax({
    url,
    success(result) {
      $button.removeClass("disabled");
      $buttonWrapper.removeClass("unassigned");
      $buttonWrapper.attr("room-id", result.room_id);
      $button.html(`<i class="far fa-building"></i> ${result.room_name}`);
      $(`.room span[round-id=${roundId}] .room-toggle`).css(
        "background-color",
        result.room_color,
      );
    },
  });
}

function populateAlternativeRooms() {
  const $parent = $(this).parent();
  const roomId = $parent.attr("room-id");
  const roundId = $parent.attr("round-id");
  const outround = $parent.attr("outround") === "true";
  const baseUrl = outround ? "/outround" : "/round";
  const url = `${baseUrl}/${roundId}/alternative_rooms/${roomId || ""}`;

  $.ajax({
    url,
    success(result) {
      $parent.find(".dropdown-menu").html(result);
      $parent.find(".dropdown-menu").find(".room-assign").click(assignRoom);
      quickSearchInit($parent.find("#quick-search"));
      $parent.find("#quick-search").focus();
    },
  });
}

function populateAlternativeTeams() {
  const $parent = $(this).parent();
  const teamId = $parent.attr("team-id");
  const roundId = $parent.attr("round-id");
  const position = $parent.attr("position");
  const url = `/round/${roundId}/${teamId}/alternative_teams/${position}`;

  $.ajax({
    url,
    success(result) {
      $parent.find(".dropdown-menu").html(result);
      $parent.find(".dropdown-menu").find(".team-assign").click(assignTeam);
      quickSearchInit($parent.find("#quick-search"));
      $parent.find("#quick-search").focus();
    },
  });
}

function assignJudge(e) {
  e.preventDefault();
  const roundId = $(e.target).attr("round-id");
  const judgeId = $(e.target).attr("judge-id");
  const curJudgeId = $(e.target).attr("current-judge-id");
  const url = `/round/${roundId}/assign_judge/${judgeId}/${curJudgeId || ""}`;

  let $buttonWrapper;
  if (curJudgeId) {
    $buttonWrapper = $(`span[round-id=${roundId}][judge-id=${curJudgeId}]`);
  } else {
    $buttonWrapper = $(`span[round-id=${roundId}].unassigned`).first();
  }
  const $button = $buttonWrapper.find(".btn-sm");
  $button.addClass("disabled");

  $.ajax({
    url,
    success(result) {
      $button.removeClass("disabled");
      $buttonWrapper.removeClass("unassigned");
      $buttonWrapper.attr("judge-id", result.judge_id);

      const rank = result.judge_rank.toFixed(2);
      $button.html(`${result.judge_name} <small>(${rank})</small>`);
      $(`.judges span[round-id=${roundId}] .judge-toggle`).removeClass("chair");
      $(`.judges span[round-id=${roundId}][judge-id=${result.chair_id}]
        .judge-toggle`).addClass("chair");
    },
  });
}

function populateAlternativeJudges() {
  const $parent = $(this).parent();
  const judgeId = $parent.attr("judge-id");
  const roundId = $parent.attr("round-id");
  const url = `/round/${roundId}/alternative_judges/${judgeId || ""}`;

  $.ajax({
    url,
    success(result) {
      $parent.find(".dropdown-menu").html(result);
      $parent.find(".dropdown-menu").find(".judge-assign").click(assignJudge);
      quickSearchInit($parent.find("#quick-search"));
      $parent.find("#quick-search").focus();
    },
  });
}

function lazyLoad(element, url) {
  element.addClass("loading");
  $.ajax({
    url,
    success(result) {
      element.html(result);
      element.removeClass("loading");
    },
    failure() {
      element.html("Error received from server");
      element.removeClass("loading");
    },
  });
}

function alertLink() {
  window.alert(`Note that you have assigned a judge from within the pairing.
    You need to go and fix that round now.`);
}

function togglePairingRelease(event) {
  event.preventDefault();
  $.ajax({
    url: "/pairing/release",
    success(result) {
      if (result.pairing_released) {
        $("#close-pairings").removeClass("d-none");
        $("#release-pairings").addClass("d-none");
      } else {
        $("#close-pairings").addClass("d-none");
        $("#release-pairings").removeClass("d-none");
      }
    },
  });
}

function handleJudgeRemoveClick(event) {
  event.preventDefault();
  const roundId = $(this).attr("round-id");
  const judgeId = $(this).attr("judge-id");

  const isOutround = $(this).hasClass("outround-judge-remove");
  const endpointPrefix = isOutround ? "outround" : "round";

  function handleRemoveSuccess(response) {
    if (response.success) {
      window.location.reload();
    } else {
      alert("Failed to remove judge.");
    }
  }

  function handleRemoveError() {
    alert("Error removing judge.");
  }

  $.ajax({
    url: `/${endpointPrefix}/${roundId}/remove_judge/${judgeId}/`,
    dataType: "json",
    success: handleRemoveSuccess,
    error: handleRemoveError,
  });
}

function handleChairClick(event) {
  event.preventDefault();
  const roundId = $(this).attr("round-id");
  const judgeId = $(this).attr("judge-id");

  const isOutround = $(this).hasClass("outround-judge-chair");
  const endpointPrefix = isOutround ? "outround" : "round";

  function handleAssignSuccess(response) {
    if (response.success) {
      window.location.reload();
    } else {
      alert("Failed to assign chair.");
    }
  }

  function handleAssignError() {
    alert("Error assigning chair.");
  }

  $.ajax({
    url: `/${endpointPrefix}/${roundId}/assign_chair/${judgeId}/`,
    dataType: "json",
    success: handleAssignSuccess,
    error: handleAssignError,
  });
}

$(document).ready(() => {
  populateTabCards();
  $("#team_ranking").each((_, element) => {
    lazyLoad($(element).parent(), "/team/rank/");
  });
  $("#debater_ranking").each((_, element) => {
    lazyLoad($(element).parent(), "/debater/rank/");
  });
  $(".judge-toggle").click(populateAlternativeJudges);
  $(".team-toggle").click(populateAlternativeTeams);
  $(".room-toggle").click(populateAlternativeRooms);
  $(".alert-link").click(alertLink);
  $(".btn.release").click(togglePairingRelease);

  $(document).on(
    "click",
    ".judge-chair, .outround-judge-chair",
    handleChairClick,
  );

  $(document).on(
    "click",
    ".judge-remove, .outround-judge-remove",
    handleJudgeRemoveClick,
  );

  $(document).on("click", ".team-switch-sides", handleSwitchSides);
});
