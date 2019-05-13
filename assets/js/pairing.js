import $ from "jquery";

import quickSearchInit from "./quickSearch";

function populateTabCard(tabCardElement) {
  const teamId = tabCardElement.attr("team-id");
  $.ajax({
    url: `/team/${teamId}/stats`,
    success(result) {
      const stats = result.result;
      const text = [
        stats.wins,
        stats.total_speaks.toFixed(2),
        stats.govs,
        stats.opps,
        stats.seed
      ].join(" / ");
      tabCardElement.attr("title", "Wins / Speaks / Govs / Opps / Seed");
      tabCardElement.attr("href", `/team/card/${teamId}`);
      tabCardElement.text(text);
    }
  });
}

function assignTeam(e) {
  e.preventDefault();
  const teamId = $(e.target).attr("team-id");
  const oldTeamId = $(e.target).attr("src-team-id");
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

        populateTabCard($(`.tabcard[team-id=${result.team.id}]`));

        const $oldTeamTabCard = $(`.tabcard[team-id=${oldTeamId}]`);
        if ($oldTeamTabCard) {
          populateTabCard($oldTeamTabCard);
        }
      } else {
        window.alert(alertMsg);
      }
    },
    failure() {
      window.alert(alertMsg);
    }
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
      $parent
        .find(".dropdown-menu")
        .find(".team-assign")
        .click(assignTeam);
      quickSearchInit($parent.find("#quick-search"));
    }
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
    }
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
      $parent
        .find(".dropdown-menu")
        .find(".judge-assign")
        .click(assignJudge);
      quickSearchInit($parent.find("#quick-search"));
    }
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
    }
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
    }
  });
}

$(document).ready(() => {
  $(".team.tabcard").each((_, element) => {
    populateTabCard($(element));
  });
  $("#team_ranking").each((_, element) => {
    lazyLoad($(element).parent(), "/team/rank/");
  });
  $("#debater_ranking").each((_, element) => {
    lazyLoad($(element).parent(), "/debater/rank/");
  });

  $(".judge-toggle").click(populateAlternativeJudges);
  $(".team-toggle").click(populateAlternativeTeams);
  $(".alert-link").click(alertLink);
  $(".btn.release").click(togglePairingRelease);
});
