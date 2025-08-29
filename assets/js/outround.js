import $ from "jquery";

import quickSearchInit from "./quickSearch";

function cycleChoice(event) {
  const button = $(this);
  event.preventDefault();

  const outroundId = button.data("outround-id");
  $.ajax({
    url: `/outround_choice/${outroundId}`,
    success(result) {
      button.html(result.data);
    }
  });
}

function populateTabCard() {
  const roundNumber = $("#round-number").data("round-number");
  if (!roundNumber || !$(".outround-tabcard").length) {
    return;
  }
  $.ajax({
    url: `/outround/${roundNumber}/stats`,
    success(result) {
      Object.entries(result).forEach(([teamId, stats]) => {
        const tabCardElement = $(`.outround-tabcard[team-id=${teamId}]`);
        const text = [
          stats.effective_outround_seed,
          stats.outround_seed,
          stats.wins,
          stats.total_speaks.toFixed(2),
          stats.govs,
          stats.opps,
          stats.seed
        ].join(" / ");
        tabCardElement.attr(
          "title",
          "Effective Seed / Outround Seed / In-round Wins" +
            " / Speaks / Govs / Opps / Seed"
        );
        tabCardElement.attr("href", `/team/card/${teamId}`);
        tabCardElement.text(text);
      });
    }
  });
}

function assignTeam(e) {
  e.preventDefault();
  const teamId = $(e.target).attr("team-id");
  const oldTeamId = $(e.target).attr("src-team-id");
  const roundId = $(e.target).attr("round-id");
  const position = $(e.target).attr("position");
  const url = `/outround/pairings/assign_team/${roundId}/${position}/${teamId}`;
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
        $container.find(".outround-tabcard").attr("team-id", result.team.id);

        populateTabCard($(`.outround-tabcard[team-id=${result.team.id}]`));

        const $oldTeamTabCard = $(`.outround-tabcard[team-id=${oldTeamId}]`);
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
  const url = `/outround/${roundId}/${teamId}/alternative_teams/${position}`;

  $.ajax({
    url,
    success(result) {
      $parent.find(".dropdown-menu").html(result);
      $parent
        .find(".dropdown-menu")
        .find(".team-assign")
        .click(assignTeam);
      quickSearchInit($parent.find("#quick-search"));
      $parent.find("#quick-search").focus();
    }
  });
}

function assignJudge(e) {
  e.preventDefault();
  const roundId = $(e.target).attr("round-id");
  const judgeId = $(e.target).attr("judge-id");
  const curJudgeId = $(e.target).attr("current-judge-id");
  const url = `/outround/${roundId}/assign_judge/${judgeId}/${curJudgeId ||
    ""}`;

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
  const url = `/outround/${roundId}/alternative_judges/${judgeId || ""}`;

  $.ajax({
    url,
    success(result) {
      $parent.find(".dropdown-menu").html(result);
      $parent
        .find(".dropdown-menu")
        .find(".judge-assign")
        .click(assignJudge);
      quickSearchInit($parent.find("#quick-search"));
      $parent.find("#quick-search").focus();
    }
  });
}

function togglePairingRelease(event) {
  const button = $(".outround-release");
  const numTeams = button.data("num_teams");
  const typeOfRound = button.data("type_of_round");

  event.preventDefault();
  $.ajax({
    url: `/outround_pairing/release/${numTeams}/${typeOfRound}`,
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
  $(".team.outround-tabcard").each((_, element) => {
    populateTabCard($(element));
  });
  $(".choice-update").each((_, element) => {
    $(element).click(cycleChoice);
  });
  $(".outround-judge-toggle").click(populateAlternativeJudges);
  $(".outround-team-toggle").click(populateAlternativeTeams);
  $(".btn.outround-release").click(togglePairingRelease);
});
