import "../css/bracket.scss";

const SELECTOR = "#brackets-viewer-container";
const TAB_CONFIG = [
  ["list-view-tab", "list-view"],
  ["bracket-view-tab", "bracket-view"]
];

let matchMetadata = {};
const $id = id => document.getElementById(id);
const on = (node, evt, handler) => node && node.addEventListener(evt, handler);

const reshapeParticipants = (participants = [], useTeamNames) =>
  participants.map(entry => {
    const name = entry.team_name || entry.name;
    const fallback = entry.debaters_names || entry.debater_names || "";
    return { ...entry, name: useTeamNames ? name : fallback.trim() || name };
  });

const renderTeamBlock = (label, info) => {
  const name = info && info.display ? info.display : "TBD";
  const members =
    info && info.debaters
      ? `<div class="team-members text-muted small mt-1">${info.debaters}</div>`
      : "";
  return (
    `<div class="match-team mb-3"><strong>${label}:</strong>` +
    `<span class="team-name d-block font-weight-bold">${name}</span>` +
    `${members}</div>`
  );
};

const renderBracket = () => {
  const viewer = window.bracketsViewer;
  const container = document.querySelector(SELECTOR);
  const payload = window.bracketViewerData || window.bracketData || null;
  if (
    !viewer ||
    typeof viewer.render !== "function" ||
    !container ||
    !payload
  ) {
    return;
  }

  const useTeamNames = document.body.classList.contains("show-team-names");
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
    const tabNode = $id(tab);
    const paneNode = $id(pane);
    const active = pane === targetPane;
    if (tabNode) {
      tabNode.classList.toggle("active", active);
      tabNode.setAttribute("aria-selected", active);
    }
    if (paneNode) {
      paneNode.classList.toggle("active", active);
    }
  });

  const showBracket = targetPane === "bracket-view";
  document.body.classList.toggle("bracket-view-active", showBracket);
  if (showBracket && render) {
    renderBracket();
  }
};

const openModal = metadata => {
  const modal = $id("match-metadata-modal");
  const backdrop = $id("match-modal-backdrop");
  const content = $id("modal-content");
  if (!modal || !backdrop || !content) return;

  const judges =
    (metadata.judges || [])
      .map(judge => {
        const name = judge.is_chair
          ? `<strong>${judge.name}</strong>`
          : judge.name;
        const cls = judge.is_chair ? "judge-name is-chair" : "judge-name";
        return `<div class="${cls}">${name}</div>`;
      })
      .join("") || '<span class="text-muted">TBD</span>';

  content.innerHTML = `
    <button id="close-modal" class="close text-muted position-absolute"
            style="right: 0;" aria-label="Close">
      <span aria-hidden="true">&times;</span>
    </button>
    <div class="match-teams mb-3">
      ${renderTeamBlock("Government", metadata.gov_team)}
      ${renderTeamBlock("Opposition", metadata.opp_team)}
    </div>
    <div class="match-room mb-3"><strong>Room:</strong> ${metadata.room ||
      "TBD"}</div>
    <div class="match-judges">
      <strong>Judges:</strong>
      <div class="judge-list mt-2">${judges}</div>
    </div>`;

  modal.style.display = "block";
  backdrop.style.display = "block";
};

const closeModal = () => {
  const modal = $id("match-metadata-modal");
  const backdrop = $id("match-modal-backdrop");
  if (modal) modal.style.display = "none";
  if (backdrop) backdrop.style.display = "none";
};

const initTabs = () => {
  TAB_CONFIG.forEach(([tabId, pane]) => {
    on($id(tabId), "click", evt => {
      evt.preventDefault();
      const render = pane === "bracket-view";
      activateTab(pane, { render });
    });
  });

  on($id("name_display_toggle"), "change", evt => {
    const useTeamNames = !evt.target.checked;
    document.body.classList.toggle("show-team-names", useTeamNames);
    if (document.body.classList.contains("bracket-view-active")) {
      renderBracket();
    }
  });

  const bracketPane = $id("bracket-view");
  const shouldRender =
    window.location.hash === "#bracket-view" ||
    (bracketPane && bracketPane.classList.contains("active"));
  if (shouldRender) {
    activateTab("bracket-view", { render: true });
  }
};

on(document, "DOMContentLoaded", initTabs);
on(document, "click", evt => {
  const { target } = evt;
  const backdropHit = target.closest
    ? target.closest("#match-modal-backdrop")
    : null;
  if (target.id === "close-modal" || backdropHit) {
    evt.preventDefault();
    closeModal();
    return;
  }

  const matchNode = target.closest
    ? target.closest(`${SELECTOR} .match`)
    : null;
  if (!matchNode) return;

  evt.preventDefault();
  const matchId =
    (matchNode.dataset && matchNode.dataset.matchId) ||
    matchNode.getAttribute("data-match-id");
  const metadata = matchMetadata[matchId];
  if (metadata) {
    openModal(metadata);
  }
});

on(document, "keydown", evt => {
  if (evt.key === "Escape") closeModal();
});
