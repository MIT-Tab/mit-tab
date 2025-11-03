import "../css/public-home.scss";

function collectStatusBlocks(container) {
  return Array.from(container.querySelectorAll("[data-status]"))
    .map((block) => {
      const shell = block.querySelector("[data-status-icon]");
      const primary = block.querySelector("[data-status-primary]");
      const secondary = block.querySelector("[data-status-secondary]");

      if (!shell || !primary || !secondary) {
        return null;
      }

      return {
        reset() {
          shell.className = "status-icon";
        },
        apply(primaryText, secondaryText) {
          primary.textContent = primaryText;
          secondary.textContent = secondaryText;
        },
      };
    })
    .filter(Boolean);
}

function getPairingStatusText(pairingReleased) {
  return pairingReleased ? "Pairing released" : "Pairing in progress";
}

function readState(container) {
  const toInt = (value) => {
    const parsed = Number.parseInt(value, 10);
    return Number.isNaN(parsed) ? 0 : parsed;
  };

  const toBool = (value) => value === "true";

  return {
    curRound: toInt(container.dataset.curRound),
    totalRounds: toInt(container.dataset.totalRounds),
    pairingReleased: toBool(container.dataset.pairingReleased),
    inOutrounds: toBool(container.dataset.inOutrounds),
    currentOutroundLabel: (container.dataset.currentOutroundLabel || "").trim(),
  };
}

function updateStatus(blocks, state) {
  if (!blocks.length) {
    return;
  }

  const {
    curRound,
    totalRounds,
    pairingReleased,
    inOutrounds,
    currentOutroundLabel,
  } = state;

  const pairingText = getPairingStatusText(pairingReleased);
  const resetBlocks = () => {
    blocks.forEach((block) => {
      block.reset();
    });
  };

  const applyToBlocks = (primaryText, secondaryText) => {
    blocks.forEach((block) => {
      block.apply(primaryText, secondaryText);
    });
  };
  if (curRound <= 1) {
    resetBlocks();
    applyToBlocks("Tournament", "Starting soon");
    return;
  }

  if (inOutrounds) {
    resetBlocks();
    applyToBlocks(currentOutroundLabel || "Elimination rounds", pairingText);
    return;
  }

  if (curRound > 1 && curRound <= totalRounds + 1) {
    const displayRound = curRound;
    resetBlocks();
    applyToBlocks(`Round ${displayRound}`, pairingText);
    return;
  }

  resetBlocks();
  applyToBlocks("Tournament", pairingText);
}

function initializePublicHome() {
  const container = document.querySelector("[data-public-home]");
  if (!container) {
    return;
  }

  const state = readState(container);
  if (!state) {
    return;
  }

  const statusBlocks = collectStatusBlocks(container);
  updateStatus(statusBlocks, state);
}

document.addEventListener("DOMContentLoaded", initializePublicHome);
