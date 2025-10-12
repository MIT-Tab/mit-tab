import "../css/bracket.scss";
import $ from "jquery";

const SELECTOR = "#brackets-viewer-container";
const TAB_CONFIG = [
  ["list-view-tab", "list-view"],
  ["bracket-view-tab", "bracket-view"]
];

let matchMetadata = {};

const reshapeParticipants = (participants = [], useTeamNames) =>
  participants.map(entry => {
    const name = entry.team_name;
    const debaters = entry.debaters_names || "";
    return { ...entry, name: useTeamNames ? name : debaters.trim() || name };
  });

const updateTeam = (teamKey, info = {}, fallbackLabel) => {
  const $team = $(`#match-metadata-modal [data-team="${teamKey}"]`);
  if (!$team.length) return;

  const $label = $team.find("[data-team-label]");
  if ($label.length) {
    const seed = info && info.seed != null ? info.seed : null;
    const labelText =
      seed != null ? `Seed ${seed}` : info.label || fallbackLabel;
    $label.text(labelText || fallbackLabel);
  }

  const name = typeof info.name === "string" ? info.name.trim() : "";
  const displayName =
    name || (typeof info.display === "string" ? info.display.trim() : "");
  $team.find("[data-team-name]").text(displayName || "TBD");

  const rawMembers =
    typeof info.debaters_plain === "string" ? info.debaters_plain.trim() : "";
  const members =
    rawMembers ||
    (typeof info.debaters === "string" ? info.debaters.trim() : "");
  const $members = $team.find("[data-team-members]");
  if ($members.length) {
    $members.text(members);
    $members.toggleClass("d-none", !members);
  }
};

const renderJudges = ($modal, judges) => {
  const $list = $modal.find("[data-judges]");
  if (!$list.length) return;

  $list.empty();
  const hasJudges = Array.isArray(judges) && judges.length;
  const lineup = hasJudges ? judges : [{ name: "TBD" }];

  lineup.forEach(judge => {
    const name =
      judge && typeof judge.name === "string" && judge.name.trim()
        ? judge.name.trim()
        : "TBD";

    const $item = $("<li/>", { class: "judge-pill" });
    if (judge && judge.is_chair) $item.addClass("is-chair");

    $("<span/>", { class: "judge-pill__name", text: name }).appendTo($item);

    if (judge && judge.is_chair) {
      $("<span/>", { class: "judge-pill__badge", text: "Chair" }).appendTo(
        $item
      );
    }

    if (!hasJudges) $item.addClass("text-muted");
    $list.append($item);
  });
};

const modalIsOpen = () => !$("#match-metadata-modal").prop("hidden");

const renderBracket = () => {
  const viewer = window.bracketsViewer;
  const $container = $(SELECTOR);
  const payload = window.bracketViewerData;
  if (!viewer || !viewer.render || !$container.length || !payload) return;

  const useTeamNames = $("body").hasClass("show-team-names");
  const data = {
    ...payload,
    participants: reshapeParticipants(payload.participants, useTeamNames)
  };
  viewer.render(data, { selector: SELECTOR, clear: true });
  matchMetadata = payload.match_metadata || {};
};

window.renderBracket = renderBracket;

const activateTab = (targetPane, { render = false } = {}) => {
  TAB_CONFIG.forEach(([tab, pane]) => {
    const $tab = $(`#${tab}`);
    const $pane = $(`#${pane}`);
    const active = pane === targetPane;

    $tab.toggleClass("active", active).attr("aria-selected", active);
    $pane.toggleClass("active", active);
  });

  const showBracket = targetPane === "bracket-view";
  $("body").toggleClass("bracket-view-active", showBracket);
  if (showBracket && render) renderBracket();
};

const openModal = metadata => {
  const $modal = $("#match-metadata-modal");
  const $backdrop = $("#match-modal-backdrop");
  if (!$modal.length || !$backdrop.length || !metadata) return;

  updateTeam("gov", metadata.gov_team || {}, "Team 1");
  updateTeam("opp", metadata.opp_team || {}, "Team 2");

  $modal.find("[data-room]").text(metadata.room || "TBD");
  renderJudges($modal, metadata.judges);

  $modal.removeAttr("hidden").attr("aria-hidden", "false");
  $backdrop.removeAttr("hidden");
  document.body.classList.add("match-modal-open");

  const closeButton = document.getElementById("close-modal");
  if (closeButton) closeButton.focus();
};

const closeModal = () => {
  const $modal = $("#match-metadata-modal");
  const $backdrop = $("#match-modal-backdrop");
  if (!$modal.length || !$backdrop.length || $modal.prop("hidden")) return;

  $modal.attr("hidden", true).attr("aria-hidden", "true");
  $backdrop.attr("hidden", true);
  document.body.classList.remove("match-modal-open");
};

const initTabs = () => {
  TAB_CONFIG.forEach(([tabId, pane]) => {
    $(`#${tabId}`).on("click", evt => {
      evt.preventDefault();
      activateTab(pane, { render: pane === "bracket-view" });
    });
  });

  $("#name_display_toggle").on("change", evt => {
    const useTeamNames = !evt.target.checked;
    $("body").toggleClass("show-team-names", useTeamNames);
    if ($("body").hasClass("bracket-view-active")) renderBracket();
  });

  const $bracketPane = $("#bracket-view");
  const shouldRender =
    window.location.hash === "#bracket-view" || $bracketPane.hasClass("active");
  if (shouldRender) activateTab("bracket-view", { render: true });
};

$(document).ready(initTabs);

$(document).on("click", "#close-modal", evt => {
  evt.preventDefault();
  closeModal();
});

$(document).on("click", "#match-modal-backdrop", evt => {
  evt.preventDefault();
  closeModal();
});

$(document).on("click", `${SELECTOR} .match`, function handleMatchClick(evt) {
  evt.preventDefault();
  const matchId = this.dataset.matchId || $(this).attr("data-match-id");
  const metadata = matchMetadata[matchId];
  if (metadata) openModal(metadata);
});

$(document).on("keydown", evt => {
  if (evt.key === "Escape" && modalIsOpen()) closeModal();
});
