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

const renderTeamBlock = (label, info) => {
  const name = info && info.display ? info.display : "TBD";
  const members =
    info && info.debaters
      ? `<div class="team-members text-muted small mt-1">${info.debaters}</div>`
      : "";
  return (
    `<div class="match-team mb-3"><strong>${label}</strong>` +
    `<span class="team-name d-block fw-bold">${name}</span>` +
    `${members}</div>`
  );
};

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
  // Bootstrap 5: Use Bootstrap's Tab API
  const targetTab = targetPane === "list-view" ? "list-view-tab" : "bracket-view-tab";
  const tabElement = document.getElementById(targetTab);
  
  if (tabElement) {
    const tab = new bootstrap.Tab(tabElement);
    tab.show();
  }

  const showBracket = targetPane === "bracket-view";
  $("body").toggleClass("bracket-view-active", showBracket);
  if (showBracket && render) renderBracket();
};

const openModal = metadata => {
  const $modal = $("#match-metadata-modal");
  const $backdrop = $("#match-modal-backdrop");
  const $content = $("#modal-content");
  if (!$modal.length || !$backdrop.length || !$content.length) return;

  const judges =
    metadata.judges && metadata.judges.length
      ? metadata.judges
          .map(judge => {
            const name = judge.is_chair
              ? `<strong>${judge.name}</strong>`
              : judge.name;
            const cls = judge.is_chair ? "judge-name is-chair" : "judge-name";
            return `<div class="${cls}">${name}</div>`;
          })
          .join("")
      : '<span class="text-muted">TBD</span>';

  $content.html(`
    <button id="close-modal" class="close text-muted position-absolute"
            style="right: 0;" aria-label="Close">
      <span aria-hidden="true">&times;</span>
    </button>
    <div class="match-teams mb-3">
      ${renderTeamBlock("Team 1: ", metadata.gov_team)}
      ${renderTeamBlock("Team 2: ", metadata.opp_team)}
    </div>
    <div class="match-room mb-3"><strong>Room:</strong> ${metadata.room ||
      "TBD"}</div>
    <div class="match-judges">
      <strong>Judges:</strong>
      <div class="judge-list mt-2">${judges}</div>
    </div>`);

  $modal.show();
  $backdrop.show();
};

const closeModal = () => {
  $("#match-metadata-modal").hide();
  $("#match-modal-backdrop").hide();
};

const initTabs = () => {
  // Bootstrap 5: Listen for tab events
  TAB_CONFIG.forEach(([tabId, pane]) => {
    const tabEl = document.getElementById(tabId);
    if (tabEl) {
      tabEl.addEventListener('shown.bs.tab', () => {
        if (pane === "bracket-view") renderBracket();
      });
      
      // Also support direct clicks
      $(`#${tabId}`).on("click", evt => {
        evt.preventDefault();
        activateTab(pane, { render: pane === "bracket-view" });
      });
    }
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

$(document).on("click", evt => {
  const $target = $(evt.target);
  const $backdropHit = $target.closest("#match-modal-backdrop");

  if (evt.target.id === "close-modal" || $backdropHit.length) {
    evt.preventDefault();
    closeModal();
    return;
  }

  const $matchNode = $target.closest(`${SELECTOR} .match`);
  if (!$matchNode.length) return;

  evt.preventDefault();
  const matchId =
    $matchNode.data("match-id") || $matchNode.attr("data-match-id");
  const metadata = matchMetadata[matchId];
  if (metadata) openModal(metadata);
});

$(document).on("keydown", evt => {
  if (evt.key === "Escape") closeModal();
});
