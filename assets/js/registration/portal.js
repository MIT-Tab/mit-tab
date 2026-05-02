import $ from "jquery";
import "select2";
import "select2/dist/css/select2.css";
import "@ttskch/select2-bootstrap4-theme/dist/select2-bootstrap4.min.css";

const NEW_VALUE = "__new__";
const DEB_URL = (id) => `/registration/api/debaters/${id}/`;

const customSchools = [];
const customDebaters = {};

const getRegistrationSelect = () =>
  $("[data-school-select='registration']").first();

const getRegistrationSchoolValue = () => getRegistrationSelect().val() || "";

const getOptionLabel = ($select, value) => {
  if (!value) return "";
  const option = $select.find(`option[value="${value}"]`);
  return option.length ? option.text() : "";
};

const getRegistrationSchoolLabel = () => {
  const $select = getRegistrationSelect();
  return getOptionLabel($select, $select.val());
};

const findSchoolValueByName = (name) => {
  const normalized = name.trim().toLocaleLowerCase();
  let match = "";
  $("[data-school-select='registration']")
    .first()
    .find("option")
    .each((_, option) => {
      if (
        !match &&
        (option.textContent || "").trim().toLocaleLowerCase() === normalized
      ) {
        match = option.value;
      }
    });
  return match;
};

const setPlaceholder = ($select, message, disabled = true) => {
  $select.empty().append(`<option value="">${message}</option>`);
  $select.prop("disabled", disabled);
};

const sortOptions = ($select) => {
  const current = $select.val();
  const options = $select.find("option").get();
  if (!options.length) return;
  const [first, ...rest] = options;
  rest.sort((a, b) =>
    (a.text || "").localeCompare(b.text || "", undefined, {
      sensitivity: "base",
    }),
  );
  $select.empty();
  if (first) {
    $select.append(first);
  }
  rest.forEach((option) => $select.append(option));
  if (current && $select.find(`option[value="${current}"]`).length) {
    $select.val(current);
  }
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
    ?.querySelector(`[name$="${selectEl.dataset.debaterInput}_novice_status"]`);
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
  const currentValue = $select.val() || $select.data("currentValue") || "";
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
      option.value = debater.name;
      option.textContent = debater.name;
      option.dataset.debater = JSON.stringify(debater);
      $select.append(option);
    });
  }
  sortOptions($select);
  const restoredValue = $select.find(`option[value="${currentValue}"]`).length
    ? currentValue
    : "";
  $select.val(restoredValue);
  $select.prop("disabled", false);
  syncDebater($select[0]);
  // Notify listeners (summary header, etc.) that the value has changed
  // — needed because programmatic .val() does not fire a change event.
  $select.trigger("change");
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
    option.value = debater.name;
    option.textContent = debater.name;
    option.dataset.debater = JSON.stringify(debater);
    $debaterSelect.append(option);
    sortOptions($debaterSelect);
  });
};

const loadDebaters = ($schoolSelect) => {
  const listId = $schoolSelect.data("nameId");
  if (!listId) return;
  const $debaterSelect = $(`#${listId}`);
  if (!$debaterSelect.length) return;
  const value = $schoolSelect.val();
  if (!value) {
    $schoolSelect.removeData("loadedDebaterSchool");
    setPlaceholder($debaterSelect, "Select a school first");
    return;
  }
  if (
    $schoolSelect.data("loadedDebaterSchool") === value &&
    $debaterSelect.find("option").length > 1
  ) {
    return;
  }
  if (value.startsWith("custom:")) {
    $schoolSelect.data("loadedDebaterSchool", value);
    renderDebaterList($debaterSelect, [], value);
    return;
  }
  if (!value.startsWith("apda:")) {
    $schoolSelect.removeData("loadedDebaterSchool");
    setPlaceholder($debaterSelect, "Select a school first");
    return;
  }
  $schoolSelect.data("loadedDebaterSchool", value);
  setPlaceholder($debaterSelect, "Loading debaters...");
  $.getJSON(DEB_URL(value.split(":")[1]))
    .done((data) => {
      const entries = Array.isArray(data) ? data : data.debaters || [];
      renderDebaterList($debaterSelect, entries, value);
    })
    .fail(() => {
      $schoolSelect.removeData("loadedDebaterSchool");
      setPlaceholder($debaterSelect, "Unable to load debaters");
    });
};

const toggleAddTeamButton = () => {
  const $addButton = $("[data-add-form='team']");
  const hasSchool = Boolean(getRegistrationSchoolValue());
  $addButton.prop("disabled", !hasSchool);
};

const initJudgeSchoolSelect = ($scope = $(document)) => {
  $scope.find("[data-judge-school-select]").each((_, el) => {
    const $select = $(el);
    if ($select.data("select2")) return;
    if (!$select.find('option[value=""]').length) {
      $select.prepend('<option value=""></option>');
    }
    $select.select2({
      theme: "bootstrap4",
      width: "100%",
      placeholder: "Select schools",
      closeOnSelect: false,
    });
  });
};

const refreshJudgeSchoolSelects = () => {
  $("[data-judge-school-select]").each((_, el) => {
    const $select = $(el);
    if ($select.data("select2")) {
      $select.trigger("change.select2");
    }
  });
};

const updateManagementForm = (prefix, delta) => {
  const $total = $(`[name="${prefix}-TOTAL_FORMS"]`);
  $total.val(parseInt($total.val(), 10) + delta);
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

const truncate = (text, max = 60) => {
  if (!text) return "";
  return text.length > max ? `${text.slice(0, max - 1).trim()}…` : text;
};

const readSelectedText = ($el) => {
  if (!$el || !$el.length) return "";
  if ($el.is("select")) {
    // Skip placeholder/loading options — they always have value=""
    const value = $el.val();
    if (!value) return "";
    return ($el.find("option:selected").text() || "").trim();
  }
  return ($el.val() || "").toString().trim();
};

const updateSubformSummary = ($subform) => {
  const $summaryName = $subform.find("[data-subform-summary-name]");
  const $summaryDetail = $subform.find("[data-subform-summary-detail]");
  if (!$summaryName.length) return;
  const type = $subform.data("form");

  if (type === "team") {
    const teamName = readSelectedText(
      $subform.find('input[name$="-name"]').first(),
    );
    const d1 = readSelectedText(
      $subform.find('select[name$="debater_one_name"]'),
    );
    const d2 = readSelectedText(
      $subform.find('select[name$="debater_two_name"]'),
    );
    $summaryName.text(teamName || "Untitled team");
    $summaryName.toggleClass(
      "registration-subform__name--placeholder",
      !teamName,
    );
    const debaters = [d1, d2].filter(Boolean).join(" · ");
    $summaryDetail.text(truncate(debaters));
  } else if (type === "judge") {
    const judgeName = readSelectedText(
      $subform.find('input[name$="-name"]').first(),
    );
    const email = readSelectedText(
      $subform.find('input[name$="-email"]').first(),
    );
    $summaryName.text(judgeName || "Untitled judge");
    $summaryName.toggleClass(
      "registration-subform__name--placeholder",
      !judgeName,
    );
    $summaryDetail.text(truncate(email));
  }
};

const subformHasErrors = ($subform) =>
  $subform.find(
    ".alert-danger, .registration-field__error, .text-danger, .invalid-feedback",
  ).length > 0;

const collapseSubformIfPopulated = ($subform) => {
  if ($subform.data("forceExpanded")) return;
  if (subformHasErrors($subform)) return;
  const nameVal = ($subform
    .find('input[name$="-name"]')
    .first()
    .val() || ""
  ).trim();
  if (nameVal) {
    $subform.addClass("is-collapsed");
    $subform
      .find("[data-subform-toggle]")
      .attr("aria-expanded", "false");
  }
};

const initSubforms = ($scope) => {
  $scope.find("[data-form]").each((_, el) => {
    const $subform = $(el);
    if ($subform.data("subformInitialized")) return;
    $subform.data("subformInitialized", true);
    updateSubformSummary($subform);
    collapseSubformIfPopulated($subform);
  });
};

const appendSchoolOption = (value, name) => {
  $("[data-school-select]").each((_, el) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = name;
    el.append(option);
  });
  $("[data-judge-school-select]").each((_, el) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = name;
    el.append(option);
  });
  refreshJudgeSchoolSelects();
};

const ensureOptionExists = ($select, value, label) => {
  if (!value) return;
  if (!$select.find(`option[value="${value}"]`).length) {
    $select.append(
      $("<option>")
        .val(value)
        .text(label || "Selected School"),
    );
  }
};

const syncSchoolOptionsFromRegistration = ($select) => {
  if ($select.is('[data-school-select="registration"]')) return;
  const $registration = getRegistrationSelect();
  if (!$registration.length) return;
  const regOptions = $registration.children();
  if (regOptions.length <= 1) return;
  const currentOptions = $select.children();
  if (currentOptions.length > 2 && currentOptions.length >= regOptions.length) {
    return;
  }
  const currentValue = $select.val();
  const currentLabel = getOptionLabel($select, currentValue);
  $select.empty();
  regOptions.each((_, option) => {
    $select.append($(option).clone());
  });
  if (currentValue) {
    if ($select.find(`option[value="${currentValue}"]`).length) {
      $select.val(currentValue);
    } else {
      ensureOptionExists($select, currentValue, currentLabel);
      $select.val(currentValue);
    }
  }
};

const maybePrefillFromRegistration = ($select) => {
  if (!$select.data("prefillFromRegistration") || $select.val()) {
    return;
  }
  syncSchoolOptionsFromRegistration($select);
  const regValue = getRegistrationSchoolValue();
  if (!regValue) return;
  const regLabel = getRegistrationSchoolLabel();
  ensureOptionExists($select, regValue, regLabel);
  $select.data("prefillAuto", true);
  $select.val(regValue).trigger("change");
  $select.data("prefillAuto", false);
  $select.data("prefillManual", false);
};

const prefillTeamSchools = () => {
  const regValue = getRegistrationSchoolValue();
  if (!regValue) return;
  const regLabel = getRegistrationSchoolLabel();
  $("[data-prefill-from-registration='true']").each((_, element) => {
    const $select = $(element);
    syncSchoolOptionsFromRegistration($select);
    if ($select.data("prefillManual")) {
      return;
    }
    ensureOptionExists($select, regValue, regLabel);
    const isMultiple = $select.prop("multiple");
    const currentValue = $select.val();
    if (isMultiple) {
      const values = Array.isArray(currentValue) ? [...currentValue] : [];
      if (!values.includes(regValue)) {
        values.push(regValue);
      }
      $select.data("prefillAuto", true);
      $select.val(values).trigger("change");
      $select.data("prefillAuto", false);
      return;
    }
    if (!isMultiple && currentValue === regValue) {
      return;
    }
    $select.data("prefillAuto", true);
    $select.val(regValue).trigger("change");
    $select.data("prefillAuto", false);
  });
};

const configureSchoolSelect = ($select) => {
  syncSchoolOptionsFromRegistration($select);
  toggleNewSchoolInput($select);
  maybePrefillFromRegistration($select);
  loadDebaters($select);
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
  initJudgeSchoolSelect($element);
  prefillTeamSchools();
  $element.data("forceExpanded", true);
  $element.data("subformInitialized", true);
  updateSubformSummary($element);
};

const registerQuickActions = () => {
  const $newSchoolInput = $("#new-school-name");
  $("#create-school-btn").on("click", () => {
    const name = ($newSchoolInput.val() || "").trim();
    if (!name) {
      alert("Please enter a school name");
      return;
    }
    const existingValue = findSchoolValueByName(name);
    if (existingValue) {
      alert(`School "${name}" is already available in the school list.`);
      return;
    }
    customSchools.push(name);
    const value = `custom:${encodeURIComponent(name)}`;
    appendSchoolOption(value, name);
    $newSchoolInput.val("");
    $("#new-school-modal").modal("hide");
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
      id: "",
      apda_id: "",
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
    $("#new-debater-modal").modal("hide");
  });
};

const initNewDebaterSelect = () => {
  const $mainSchool = getRegistrationSelect();
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

export default function initRegistrationPortal() {
  const $root = $("#registration-app");
  if (!$root.length) return;
  if ($root.data("registrationInitialized")) return;
  $root.data("registrationInitialized", true);
  const maxTeams = parseInt($root.data("maxTeams") || "200", 10);

  $root.find("[data-school-select]").each((_, el) => {
    configureSchoolSelect($(el));
  });
  initJudgeSchoolSelect($root);

  toggleAddTeamButton();
  prefillTeamSchools();
  registerQuickActions();
  initNewDebaterSelect();
  initSubforms($root);

  $root.on("change", "[data-school-select]", function onSchoolChange() {
    const $select = $(this);
    configureSchoolSelect($select);
    if ($select.is('[data-school-select="registration"]')) {
      toggleAddTeamButton();
      prefillTeamSchools();
    } else if (
      $select.data("prefillFromRegistration") &&
      !$select.data("prefillAuto")
    ) {
      $select.data("prefillManual", true);
    }
  });

  $root.on("change", "[data-debater-input]", function onDebChange() {
    syncDebater(this);
  });

  $root.on(
    "change",
    "[data-judge-school-select]",
    function onJudgeSchoolChange() {
      const $select = $(this);
      if (
        $select.data("prefillFromRegistration") &&
        !$select.data("prefillAuto")
      ) {
        $select.data("prefillManual", true);
      }
    },
  );

  $root.on("click", "[data-add-form]", function onAddClick(event) {
    event.preventDefault();
    addForm($(this).data("addForm"), maxTeams);
  });

  $root.on("click", "[data-remove-form]", function onRemoveClick(event) {
    event.preventDefault();
    event.stopPropagation();
    removeForm(this);
  });

  const toggleSubform = (target) => {
    const $subform = $(target).closest("[data-form]");
    $subform.toggleClass("is-collapsed");
    const isCollapsed = $subform.hasClass("is-collapsed");
    $subform
      .find("[data-subform-toggle]")
      .attr("aria-expanded", isCollapsed ? "false" : "true");
    if (!isCollapsed) {
      $subform.data("forceExpanded", true);
    }
  };

  $root.on("click", "[data-subform-toggle]", function onToggleClick(event) {
    if ($(event.target).closest("[data-remove-form]").length) return;
    event.preventDefault();
    toggleSubform(this);
  });

  $root.on("keydown", "[data-subform-toggle]", function onToggleKey(event) {
    if (event.key !== "Enter" && event.key !== " ") return;
    if ($(event.target).closest("[data-remove-form]").length) return;
    event.preventDefault();
    toggleSubform(this);
  });

  $root.on(
    "input change",
    "[data-form] input, [data-form] select",
    function onFieldChange() {
      const $subform = $(this).closest("[data-form]");
      updateSubformSummary($subform);
    },
  );

  $root.on("click", "[data-trigger-new]", function triggerNew(event) {
    event.preventDefault();
    const target = $(this).data("triggerNew");
    const $select = $(`#${target}`);
    if ($select.length) {
      $select.val(NEW_VALUE).trigger("change");
    }
  });

  // Warn the user before leaving with unsaved changes.
  // Bound at the end of init so init-time programmatic change events
  // (prefilling, debater list rebuilds) don't falsely mark the form dirty.
  const $form = $root.find("[data-registration-form]");
  if ($form.length) {
    let dirty = false;
    const markDirty = () => {
      dirty = true;
    };
    $form.on("input change", "input, select, textarea", markDirty);
    $form.on("click", "[data-add-form], [data-remove-form]", markDirty);
    $("#create-school-btn, #create-debater-btn").on("click", markDirty);
    $form.on("submit", () => {
      dirty = false;
    });
    $(window).on("beforeunload", (event) => {
      if (!dirty) return undefined;
      const native = event.originalEvent || event;
      native.returnValue = "";
      return "";
    });
  }
}
