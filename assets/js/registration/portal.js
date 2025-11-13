import $ from "jquery";

const NEW_VALUE = "__new__";
const DEB_URL = (id) => `/registration/api/debaters/${id}/`;

const customSchools = [];
const customDebaters = {};

const setPlaceholder = ($select, message, disabled = true) => {
  $select.empty().append(`<option value="">${message}</option>`);
  $select.prop("disabled", disabled);
};

const toggleNewSchoolInput = ($select) => {
  const targetId = $select.attr("id");
  $(`[data-new-school-container="${targetId}"]`).each((_, el) => {
    const $container = $(el);
    const show = $select.val() === NEW_VALUE;
    $container.toggleClass("d-none", !show);
    $container.find("input").toggleClass("d-none", !show);
  });
};

const fillDebaterData = (selectEl, debaterData = null) => {
  const { apdaTarget } = selectEl.dataset;
  const noviceField = selectEl
    .closest("[data-debater]")
    ?.querySelector(
      `input[name$="${selectEl.dataset.debaterInput}_novice_status"]`,
    );
  const qualifiedField = selectEl
    .closest("[data-debater]")
    ?.querySelector(
      `input[name$="${selectEl.dataset.debaterInput}_qualified"]`,
    );
  const apdaField = apdaTarget ? document.getElementById(apdaTarget) : null;
  if (apdaField) {
    apdaField.value =
      (debaterData && (debaterData.apda_id || debaterData.id)) || "";
  }
  if (noviceField) {
    noviceField.value = debaterData?.status === "Novice" ? "1" : "0";
  }
  if (qualifiedField) {
    qualifiedField.value = debaterData?.apda_id ? "on" : "";
  }
};

const syncDebater = (selectEl) => {
  const option = selectEl.selectedOptions && selectEl.selectedOptions[0];
  if (option && option.dataset.debater) {
    try {
      fillDebaterData(selectEl, JSON.parse(option.dataset.debater));
      return;
    } catch (error) {
      // ignore JSON parse errors
    }
  }
  fillDebaterData(selectEl);
};

const renderDebaterList = ($select, entries = [], schoolValue = "") => {
  $select.empty().append('<option value="">Select a debater</option>');
  entries.forEach((entry) => {
    const name = entry.name || entry.full_name || "";
    const option = document.createElement("option");
    option.value = name.startsWith("custom:") ? name : name;
    option.textContent = name;
    option.dataset.debater = JSON.stringify({
      id: entry.id || entry.apda_id,
      apda_id: entry.apda_id || entry.id,
      name,
      status: entry.status,
    });
    $select.append(option);
  });
  if (customDebaters[schoolValue]) {
    customDebaters[schoolValue].forEach((debater) => {
      const option = document.createElement("option");
      option.value = `custom:${debater.id}`;
      option.textContent = debater.name;
      option.dataset.debater = JSON.stringify(debater);
      $select.append(option);
    });
  }
  $select.prop("disabled", false);
};

const broadcastCustomDebater = (schoolValue, debater) => {
  $("[data-school-select]").each((_, el) => {
    if (el.value !== schoolValue) return;
    const listId = el.dataset.nameId;
    if (!listId) return;
    const $debaterSelect = $(`#${listId}`);
    if (!$debaterSelect.length) return;
    if ($debaterSelect.prop("disabled")) {
      setPlaceholder($debaterSelect, "Select a debater", false);
    }
    const option = document.createElement("option");
    option.value = `custom:${debater.id}`;
    option.textContent = debater.name;
    option.dataset.debater = JSON.stringify(debater);
    $debaterSelect.append(option);
  });
};

const loadDebaters = ($schoolSelect) => {
  const listId = $schoolSelect.data("nameId");
  if (!listId) return;
  const $debaterSelect = $(`#${listId}`);
  if (!$debaterSelect.length) return;
  const value = $schoolSelect.val();
  if (!value) {
    setPlaceholder($debaterSelect, "Select a school first");
    return;
  }
  if (value.startsWith("custom:")) {
    renderDebaterList($debaterSelect, [], value);
    return;
  }
  if (!value.startsWith("apda:")) {
    setPlaceholder($debaterSelect, "Select a school first");
    return;
  }
  setPlaceholder($debaterSelect, "Loading debaters...");
  $.getJSON(DEB_URL(value.split(":")[1]))
    .done((data) => {
      const entries = Array.isArray(data) ? data : data.debaters || [];
      renderDebaterList($debaterSelect, entries, value);
      syncDebater($debaterSelect[0]);
    })
    .fail(() => {
      setPlaceholder($debaterSelect, "Unable to load debaters");
    });
};

const configureSchoolSelect = ($select) => {
  toggleNewSchoolInput($select);
  loadDebaters($select);
};

const toggleAddTeamButton = () => {
  const $addButton = $("[data-add-form='team']");
  const hasSchool = Boolean(
    $("[data-school-select='registration']").first().val(),
  );
  $addButton.prop("disabled", !hasSchool);
};

const updateManagementForm = (prefix, delta) => {
  const $total = $(`[name="${prefix}-TOTAL_FORMS"]`);
  $total.val(parseInt($total.val(), 10) + delta);
};

const addForm = (type, maxTeams) => {
  const prefix = type === "team" ? "teams" : "judges";
  const $template = $(`#${type}-empty-form`);
  if (!$template.length) return;
  const total = parseInt($(`[name="${prefix}-TOTAL_FORMS"]`).val(), 10);
  if (type === "team" && total >= maxTeams) return;
  const html = $template.html().replace(/__prefix__/g, total);
  const $element = $(html);
  $(`[data-formset-container="${type}"]`).append($element);
  updateManagementForm(prefix, 1);
  $element.find("[data-school-select]").each((_, el) => {
    configureSchoolSelect($(el));
  });
};

const removeForm = (button) => {
  const $form = $(button).closest("[data-form]");
  const $deleteField = $form.find('input[name$="-DELETE"]');
  if ($deleteField.length) {
    $deleteField.val("on");
    $form.addClass("d-none");
  } else {
    $form.remove();
  }
};

const registerQuickActions = () => {
  const $newSchoolInput = $("#new-school-name");
  $("#create-school-btn").on("click", () => {
    const name = ($newSchoolInput.val() || "").trim();
    if (!name) {
      alert("Please enter a school name");
      return;
    }
    customSchools.push(name);
    const value = `custom:-${customSchools.length}`;
    $("[data-school-select]").each((_, el) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = name;
      el.append(option);
    });
    $newSchoolInput.val("");
    alert(`School "${name}" added.`);
  });

  $("#create-debater-btn").on("click", () => {
    const $school = $("#new-debater-school");
    const schoolValue = $school.val();
    const name = ($("#new-debater-name").val() || "").trim();
    const status = $("#new-debater-status").val() || "0";
    if (!schoolValue) {
      alert("Please select a school");
      return;
    }
    if (!name) {
      alert("Please enter a debater name");
      return;
    }
    const debater = {
      id: -Date.now(),
      apda_id: -Date.now(),
      name,
      full_name: name,
      status: status === "1" ? "Novice" : "Varsity",
      qualified: false,
    };
    if (!customDebaters[schoolValue]) {
      customDebaters[schoolValue] = [];
    }
    customDebaters[schoolValue].push(debater);
    broadcastCustomDebater(schoolValue, debater);
    $("#new-debater-name").val("");
    $("#new-debater-status").val("0");
    alert(`Debater "${name}" added.`);
  });
};

const initNewDebaterSelect = () => {
  const $mainSchool = $("[data-school-select='registration']").first();
  const $target = $("#new-debater-school");
  if (!$mainSchool.length || !$target.length) return;
  const sync = () => {
    const current = $target.val();
    $target.empty().append('<option value="">Select school</option>');
    $mainSchool.find("option").each((_, opt) => {
      if (opt.value) {
        $target.append($("<option>").val(opt.value).text(opt.textContent));
      }
    });
    if (current) {
      $target.val(current);
    }
  };
  sync();
  const observer = new MutationObserver(sync);
  observer.observe($mainSchool[0], { childList: true });
};

$(document).ready(() => {
  const $root = $("#registration-app");
  if (!$root.length) return;
  const maxTeams = parseInt($root.data("maxTeams") || "200", 10);

  $root.find("[data-school-select]").each((_, el) => {
    configureSchoolSelect($(el));
  });

  toggleAddTeamButton();
  registerQuickActions();
  initNewDebaterSelect();

  $root.on("change", "[data-school-select]", function onSchoolChange() {
    const $select = $(this);
    configureSchoolSelect($select);
    if ($select.is('[data-school-select="registration"]')) {
      toggleAddTeamButton();
    }
  });

  $root.on("change", "[data-debater-input]", function onDebChange() {
    syncDebater(this);
  });

  $root.on("click", "[data-add-form]", function onAddClick(event) {
    event.preventDefault();
    addForm($(this).data("addForm"), maxTeams);
  });

  $root.on("click", "[data-remove-form]", function onRemoveClick(event) {
    event.preventDefault();
    removeForm(this);
  });

  $root.on("click", "[data-trigger-new]", function triggerNew(event) {
    event.preventDefault();
    const target = $(this).data("triggerNew");
    const $select = $(`#${target}`);
    if ($select.length) {
      $select.val(NEW_VALUE).trigger("change");
    }
  });
});
