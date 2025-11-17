import $ from "jquery";

function normalizeBracketSize(value) {
  if (!value || value < 2) {
    return 0;
  }
  let size = 1;
  while (size < value) {
    size <<= 1;
  }
  return size;
}

function varsityRounds(startSize) {
  const rounds = [];
  if (!startSize) {
    return rounds;
  }
  let size = startSize;
  while (size >= 2) {
    rounds.push(size);
    size >>= 1;
  }
  return rounds;
}

function updateConcurrentUI({ varInput, novInput, select, label, state }) {
  const varTeams = parseInt(varInput.val(), 10) || 0;
  const novTeams = parseInt(novInput.val(), 10) || 0;
  const normalizedVar = normalizeBracketSize(varTeams);
  const rounds = varsityRounds(normalizedVar);

  label.text(`Novice Round of ${novTeams || 0} runs concurrent with`);
  select.empty();
  select.append('<option value="">No concurrent round</option>');

  if (!rounds.length) {
    select.prop("disabled", true);
    select.val("");
    state.currentSelection = "";
    return;
  }

  const hasNoviceBracket = novTeams >= 2;

  rounds.forEach((round) => {
    const disabledAttr = hasNoviceBracket && round < novTeams ? "disabled" : "";
    select.append(
      `<option value="${round}" ${disabledAttr}>Varsity Round of ${round}</option>`,
    );
  });

  select.prop("disabled", false);

  const optionValues = select
    .find("option")
    .map((_, opt) => $(opt).val())
    .get();
  if (!optionValues.includes(state.currentSelection)) {
    state.currentSelection = "";
  }
  select.val(state.currentSelection);
}

$(document).ready(() => {
  const form = $("#break-settings-form");
  if (!form.length) {
    return;
  }

  const varInput = $("#var-teams-input");
  const novInput = $("#nov-teams-input");
  const select = $("#novice-concurrent-select");
  const label = $("#novice-concurrent-label");
  const initialSelectionRaw = Number(form.data("initialConcurrent"));
  const state = {
    currentSelection:
      Number.isFinite(initialSelectionRaw) && initialSelectionRaw > 0
        ? initialSelectionRaw.toString()
        : "",
  };

  const render = () =>
    updateConcurrentUI({
      varInput,
      novInput,
      select,
      label,
      state,
    });

  varInput.on("input", render);
  novInput.on("input", render);
  select.on("change", () => {
    state.currentSelection = select.val() || "";
  });

  render();
});
