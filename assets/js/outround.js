import $ from "jquery";

import { pairingWrite, savePairingRevision } from "./pairing";
import quickSearchInit from "./quickSearch";

function cycleChoice(event) {
  const button = $(this);
  event.preventDefault();

  const outroundId = button.data("outround-id");
  const $card = button.closest(".pairing-card");
  const revision = $card.attr("data-revision");
  $.ajax({
    ...pairingWrite(
      `/outround_choice/${outroundId}`,
      revision ? { revision } : undefined,
    ),
    success(result) {
      savePairingRevision($card, result);
      button.text(result.data);
    },
    error() {
      window.alert("Unable to update the choice. Refresh and try again.");
    },
  });
}

function populateTabCards() {
  const roundNumberEl = $("#round-number");
  const roundNumbersAttr = roundNumberEl.attr("data-round-numbers");
  let roundNumbers = [];

  if (roundNumbersAttr) {
    roundNumbers = roundNumbersAttr
      .split(",")
      .map((n) => n.trim())
      .filter((n) => n);
  }

  if (!roundNumbers.length) {
    const fallbackRoundNumber = roundNumberEl.data("round-number");
    if (fallbackRoundNumber) {
      roundNumbers = [String(fallbackRoundNumber)];
    }
  }

  roundNumbers = [...new Set(roundNumbers)];

  if (!roundNumbers.length || !$(".outround-tabcard").length) {
    return;
  }

  roundNumbers.forEach((roundNumber) => {
    $.ajax({
      url: `/outround/${roundNumber}/stats/`,
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
            stats.seed,
          ].join(" / ");
          tabCardElement.attr(
            "title",
            "Effective Seed / Outround Seed / In-round Wins" +
              " / Speaks / Govs / Opps / Seed",
          );
          tabCardElement.attr("href", `/team/card/${teamId}/`);
          tabCardElement.text(text);
        });
      },
    });
  });
}

function assignTeam(e, $card, revision) {
  e.preventDefault();
  const $item = $(e.currentTarget);
  const teamId = $item.attr("team-id");
  const roundId = $item.attr("round-id");
  const position = $item.attr("position");
  const url = `/outround/pairings/assign_team/${roundId}/${position}/${teamId}/`;
  const alertMsg = `
  An error occured.
  Refresh the page and try to fix any inconsistencies you may notice.
  `;

  $.ajax({
    ...pairingWrite(url, revision ? { revision } : undefined),
    success(result) {
      if (result.success) {
        savePairingRevision($card, result);
        const $container = $(`.row[round-id=${roundId}] .${position}-team`);
        if (
          Number($container.find(".team-assign-button").attr("team-id")) !==
          Number(result.team.id)
        ) {
          $container.addClass("manual-team");
        }
        $container.find(".team-assign-button").attr("team-id", result.team.id);
        $container.find(".team-link").text(result.team.name);
        $container.find(".team-link").attr("href", `/team/${result.team.id}`);
        $container.find(".outround-tabcard").attr("team-id", result.team.id);
        populateTabCards();
      } else {
        window.alert(alertMsg);
      }
    },
    error() {
      window.alert(alertMsg);
    },
  });
}

function populateAlternativeTeams() {
  const $parent = $(this).parent();
  const teamId = $parent.attr("team-id");
  const roundId = $parent.attr("round-id");
  const position = $parent.attr("position");
  const $card = $parent.closest(".pairing-card");
  const revision = $card.attr("data-revision");
  const url = `/outround/${roundId}/${teamId}/alternative_teams/${position}/`;

  $.ajax({
    url,
    success(result) {
      const $menu = $parent.find(".dropdown-menu").html(result);
      if (revision) {
        $menu.find(".team-switch-sides").attr("data-revision", revision);
      }
      $menu
        .find(".team-assign")
        .click((event) => assignTeam(event, $card, revision));
      quickSearchInit($parent.find("#quick-search"));
      $parent.find("#quick-search").focus();
    },
  });
}

function assignJudge(e, $card, revision) {
  e.preventDefault();
  const $item = $(e.currentTarget);
  const roundId = $item.attr("round-id");
  const judgeId = $item.attr("judge-id");
  const curJudgeId = $item.attr("current-judge-id");
  const url = `/outround/${roundId}/assign_judge/${judgeId}/${
    curJudgeId ? `${curJudgeId}/` : ""
  }`;

  let $buttonWrapper;
  if (curJudgeId) {
    $buttonWrapper = $(`span[round-id=${roundId}][judge-id=${curJudgeId}]`);
  } else {
    $buttonWrapper = $(`.judges span[round-id=${roundId}].unassigned`).first();
  }
  const $button = $buttonWrapper.find(".btn-sm");
  $button.addClass("disabled");

  $.ajax({
    ...pairingWrite(url, revision ? { revision } : undefined),
    success(result) {
      savePairingRevision($card, result);
      $button.removeClass("disabled");
      $buttonWrapper.removeClass("unassigned");
      $buttonWrapper.attr("judge-id", result.judge_id);

      const rank = result.judge_rank.toFixed(2);
      $button
        .empty()
        .append(
          document.createTextNode(`${result.judge_name} `),
          $("<small>").text(`(${rank})`),
        );
      $(`.judges span[round-id=${roundId}] .outround-judge-toggle`).removeClass(
        "chair",
      );
      $(`.judges span[round-id=${roundId}][judge-id=${result.chair_id}]
    .outround-judge-toggle`).addClass("chair");
    },
    error() {
      $button.removeClass("disabled");
      window.alert("Unable to assign the judge. Refresh and try again.");
    },
  });
}

function populateAlternativeJudges() {
  const $parent = $(this).parent();
  const judgeId = $parent.attr("judge-id");
  const roundId = $parent.attr("round-id");
  const $card = $parent.closest(".pairing-card");
  const revision = $card.attr("data-revision");
  const query = window.location.search || "";
  const url = `/outround/${roundId}/alternative_judges/${judgeId ? `${judgeId}/` : ""}${query}`;

  $.ajax({
    url,
    success(result) {
      const $menu = $parent.find(".dropdown-menu").html(result);
      $menu
        .find(".outround-judge-remove, .outround-judge-chair")
        .attr("data-revision", revision);
      $menu
        .find(".judge-assign")
        .click((event) => assignJudge(event, $card, revision));
      quickSearchInit($parent.find("#quick-search"));
      $parent.find("#quick-search").focus();
    },
  });
}

function togglePairingRelease(event) {
  const button = $(event.currentTarget);
  const numTeams = button.attr("data-num_teams");
  const typeOfRound = button.attr("data-type_of_round");
  const action = button.hasClass("close-pairings-btn") ? "close" : "release";

  event.preventDefault();
  $.ajax({
    ...pairingWrite(`/outround_pairing/release/${numTeams}/${typeOfRound}/`, {
      action,
    }),
    success(result) {
      const controls = `.outround-release[data-type_of_round='${typeOfRound}']`;
      $(controls).each((_, element) => {
        const control = $(element);
        const released =
          Number(control.attr("data-num_teams")) >= result.visibility_limit;
        control.toggleClass(
          "d-none",
          control.hasClass("close-pairings-btn") !== released,
        );
      });
    },
    error() {
      window.alert("Unable to update the release. Refresh and try again.");
    },
  });
}

$(document).ready(() => {
  populateTabCards();
  $(".choice-update").each((_, element) => {
    $(element).click(cycleChoice);
  });
  $(".outround-judge-toggle").click(populateAlternativeJudges);
  $(".outround-team-toggle").click(populateAlternativeTeams);
  $(".btn.outround-release").click(togglePairingRelease);
});

$(document).on("outround:switch", () => {
  populateTabCards();
});
