const NEW = "__new__";
// Use our proxy endpoints to avoid CORS issues
const DEB_URL = (id) => `/registration/api/debaters/${id}/`;
const TEAM_SEEDS = {
  UNSEEDED: "0",
  HALF: "2",
  FULL: "3",
};
let root;

const queryAll = (selector, scope = root) =>
  Array.from(scope.querySelectorAll(selector));
const byId = (value) => document.getElementById(value);
const debaterName = (person) =>
  person.name ||
  person.full_name ||
  `${person.first_name || ""} ${person.last_name || ""}`.trim();

const updateDebaterMetaFields = (
  input,
  container,
  prefix,
  debaterData = null,
) => {
  const apdaIdField = byId(input.dataset.apdaTarget);
  const noviceField = container.querySelector(
    `input[name$="${prefix}_novice_status"]`,
  );
  const qualifiedField = container.querySelector(
    `input[name$="${prefix}_qualified"]`,
  );

  if (apdaIdField) {
    apdaIdField.value =
      (debaterData && (debaterData.apda_id || debaterData.id)) || "";
  }
  if (noviceField) {
    const noviceStatus = debaterData?.status === "Novice" ? "1" : "0";
    noviceField.value = noviceStatus;
  }
  if (qualifiedField) {
    qualifiedField.value = debaterData?.apda_id ? "on" : "";
  }
};

const setCollapseState = (trigger, target, expanded) => {
  if (!trigger || !target) {
    return;
  }
  trigger.setAttribute("aria-expanded", expanded ? "true" : "false");
  trigger.classList.toggle("is-expanded", expanded);
  if (expanded) {
    target.removeAttribute("hidden");
  } else {
    target.setAttribute("hidden", "");
  }
};

const initCollapsibles = () => {
  queryAll("[data-collapse-toggle]").forEach((trigger) => {
    const targetId = trigger.getAttribute("aria-controls");
    if (!targetId) {
      return;
    }
    const target = document.getElementById(targetId);
    if (!target) {
      return;
    }
    const defaultExpanded = trigger.getAttribute("aria-expanded") === "true";
    setCollapseState(trigger, target, defaultExpanded);
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      const expanded = trigger.getAttribute("aria-expanded") === "true";
      setCollapseState(trigger, target, !expanded);
    });
  });
};

const toggleNew = (select) => {
  queryAll(`[data-new-school-container="${select.id}"]`).forEach(
    (container) => {
      const show = select.value === NEW;
      container.classList.toggle("d-none", !show);
      const inputs = queryAll("input", container);
      for (let index = 0; index < inputs.length; index += 1) {
        const field = inputs[index];
        if (show) {
          field.classList.remove("d-none");
        } else {
          field.classList.add("d-none");
        }
      }
    },
  );
};

const normalizeQualifiedValue = (value) => {
  if (!value) return false;
  const normalized = value.toString().toLowerCase();
  return normalized === "on" || normalized === "true" || normalized === "1";
};

const updateTeamSeed = (teamForm) => {
  if (!teamForm) return;
  const seedSelect = teamForm.querySelector("[data-team-seed]");
  if (!seedSelect || seedSelect.dataset.autoset === "false") {
    return;
  }
  const firstQualified = teamForm.querySelector(
    'input[name$="debater_one_qualified"]',
  );
  const secondQualified = teamForm.querySelector(
    'input[name$="debater_two_qualified"]',
  );
  if (!firstQualified || !secondQualified) {
    return;
  }
  const first = normalizeQualifiedValue(firstQualified.value);
  const second = normalizeQualifiedValue(secondQualified.value);
  if (first && second) {
    seedSelect.value = TEAM_SEEDS.FULL;
  } else if (first || second) {
    seedSelect.value = TEAM_SEEDS.HALF;
  } else {
    seedSelect.value = TEAM_SEEDS.UNSEEDED;
  }
};

const ensureSeedSelectState = (teamForm) => {
  const seedSelect = teamForm.querySelector("[data-team-seed]");
  if (!seedSelect) {
    return;
  }
  if (!seedSelect.dataset.autoset) {
    const teamIdField = teamForm.querySelector('input[name$="team_id"]');
    const hasExistingTeam = teamIdField && teamIdField.value;
    seedSelect.dataset.autoset = hasExistingTeam ? "false" : "true";
  }
  if (seedSelect.dataset.autoset === "true") {
    updateTeamSeed(teamForm);
  }
};

const syncDebater = (input) => {
  const container = input.closest("[data-debater]");
  if (!container) return;

  const prefix = input.dataset.debaterInput;
  if (!prefix) return;

  // Find the matching option in the select itself
  const selectedOption = input.selectedOptions && input.selectedOptions[0];

  if (selectedOption && selectedOption.dataset.debater) {
    // Parse the debater data stored in the option
    try {
      const debaterData = JSON.parse(selectedOption.dataset.debater);
      updateDebaterMetaFields(input, container, prefix, debaterData);
    } catch (_error) {
      updateDebaterMetaFields(input, container, prefix);
    }
  } else {
    updateDebaterMetaFields(input, container, prefix);
  }
  const teamForm = input.closest('[data-form="team"]');
  if (teamForm) {
    ensureSeedSelectState(teamForm);
  }
};
const clearDebaterList = (list, selectElement) => {
  const targetList = list;
  const target = selectElement;
  if (targetList) {
    targetList.innerHTML = "";
  }
  if (target) {
    target.innerHTML = '<option value="">Select a school first</option>';
    target.disabled = true;
  }
};

const updateDebaters = (select) => {
  const { listId, nameId } = select.dataset;
  if (!listId || !nameId) {
    return;
  }

  let list = byId(listId);
  if (!list) {
    list = document.createElement("datalist");
    list.id = listId;
    list.style.display = "none";
    document.body.appendChild(list);
  }

  const selectElement = byId(nameId);
  if (!selectElement) {
    return;
  }

  // Handle custom schools - they only have manually created debaters
  if (select.value && select.value.startsWith("custom:")) {
    const selectedOption = select.selectedOptions && select.selectedOptions[0];
    if (selectedOption) {
      const schoolName = selectedOption.textContent;
      // Find the associated school_name hidden field
      const schoolNameField = select
        .closest("[data-debater]")
        ?.querySelector(`input[name$="_school_name"]`);
      if (schoolNameField) {
        schoolNameField.value = schoolName;
      }
    }

    // Clear and add any custom debaters for this school
    selectElement.innerHTML = '<option value="">Select a debater</option>';
    const schoolValue = select.value;
    if (window.customDebaters && window.customDebaters[schoolValue]) {
      window.customDebaters[schoolValue].forEach((debater) => {
        const option = document.createElement("option");
        option.value = `custom:${debater.id}`;
        option.textContent = debater.name;
        option.dataset.debater = JSON.stringify(debater);
        selectElement.appendChild(option);
      });
      selectElement.disabled = false;
    } else {
      selectElement.disabled = true;
    }
    return;
  }

  if (!select.value.startsWith("apda:")) {
    clearDebaterList(list, selectElement);
    return;
  }
  const apdaId = select.value.split(":")[1];
  if (!apdaId) {
    clearDebaterList(list, selectElement);
    return;
  }

  // Disable select while loading
  selectElement.disabled = true;
  selectElement.innerHTML = '<option value="">Loading debaters...</option>';

  fetch(DEB_URL(apdaId))
    .then((response) => (response.ok ? response.json() : { debaters: [] }))
    .then((data) => {
      const entries = Array.isArray(data) ? data : data.debaters || [];
      selectElement.innerHTML = "";

      // Add an empty option first
      const emptyOption = document.createElement("option");
      emptyOption.value = "";
      emptyOption.textContent = "Select a debater";
      selectElement.appendChild(emptyOption);

      // Add debater options from API
      entries.forEach((person) => {
        const option = document.createElement("option");
        const name = debaterName(person);
        option.value = name;
        option.textContent = name;

        // Store the full debater data in the option
        option.dataset.debater = JSON.stringify({
          id: person.id || person.apda_id,
          apda_id: person.apda_id || person.id,
          name,
          first_name: person.first_name,
          last_name: person.last_name,
          status: person.status,
        });

        selectElement.appendChild(option);
      });

      // Add custom debaters for this school if any exist
      const schoolValue = select.value;
      if (window.customDebaters && window.customDebaters[schoolValue]) {
        window.customDebaters[schoolValue].forEach((debater) => {
          const option = document.createElement("option");
          option.value = `custom:${debater.id}`;
          option.textContent = debater.name;
          option.dataset.debater = JSON.stringify(debater);
          selectElement.appendChild(option);
        });
      }

      // Also store in the datalist for reference, even though it is hidden
      list.innerHTML = "";
      entries.forEach((person) => {
        const option = document.createElement("option");
        const name = debaterName(person);
        option.value = name;
        option.dataset.debater = JSON.stringify({
          id: person.id || person.apda_id,
          apda_id: person.apda_id || person.id,
          name,
          first_name: person.first_name,
          last_name: person.last_name,
          status: person.status,
        });
        list.appendChild(option);
      });

      // Enable the select
      selectElement.disabled = false;
      // Trigger sync to populate hidden fields if there's a pre-selected value
      syncDebater(selectElement);
    })
    .catch(() => {
      list.innerHTML = "";
      selectElement.innerHTML =
        '<option value="">Unable to load debaters</option>';
      selectElement.disabled = true;
    });
};
const addForm = (type, maxTeams) => {
  const prefix = type === "team" ? "teams" : "judges";
  const totalInput = root.querySelector(`input[name="${prefix}-TOTAL_FORMS"]`);
  const total = parseInt(totalInput.value, 10) || 0;
  if (type === "team" && total >= maxTeams) {
    return;
  }
  const template = byId(`${type}-empty-form`);
  if (!template) {
    return;
  }
  const wrapper = document.createElement("div");
  wrapper.innerHTML = template.innerHTML.replace(/__prefix__/g, String(total));
  const element = wrapper.firstElementChild;

  // Also replace __prefix__ in data attributes
  queryAll('[data-name-id*="__prefix__"]', element).forEach((el) => {
    const elementWithNameId = el;
    elementWithNameId.dataset.nameId = elementWithNameId.dataset.nameId.replace(
      /__prefix__/g,
      String(total),
    );
  });
  queryAll('[data-list-id*="__prefix__"]', element).forEach((el) => {
    const elementWithListId = el;
    elementWithListId.dataset.listId = elementWithListId.dataset.listId.replace(
      /__prefix__/g,
      String(total),
    );
  });

  root.querySelector(`[data-formset-container="${type}"]`).appendChild(element);
  totalInput.value = total + 1;

  // If it's a team form, populate all related school selects, including debater
  // selects, from the main registration school choices.
  if (type === "team") {
    const mainSchoolSelect = root.querySelector(
      '[data-school-select="registration"]',
    );

    // First, copy all options from the main school select into the new team
    // form instance.
    const allSchoolSelects = queryAll("[data-school-select]", element);
    allSchoolSelects.forEach((selectEl) => {
      const schoolSelect = selectEl;
      if (mainSchoolSelect) {
        schoolSelect.innerHTML = "";
        queryAll("option", mainSchoolSelect).forEach((option) => {
          const newOption = option.cloneNode(true);
          schoolSelect.appendChild(newOption);
        });
      }

      if (
        mainSchoolSelect &&
        mainSchoolSelect.value &&
        mainSchoolSelect.value !== NEW &&
        mainSchoolSelect.value !== ""
      ) {
        schoolSelect.value = mainSchoolSelect.value;
        toggleNew(schoolSelect);
        if (schoolSelect.dataset.listId) {
          updateDebaters(schoolSelect);
        }
      } else {
        toggleNew(schoolSelect);
        const { listId, nameId } = schoolSelect.dataset;
        if (listId && nameId) {
          const input = byId(nameId);
          if (input) {
            input.disabled = true;
            input.placeholder = "Select a school first";
          }
        }
      }
    });
  }

  queryAll("[data-debater-input]", element).forEach((fieldEl) => {
    const field = fieldEl;
    // Debater fields should now be select elements
    const debaterContainer = field.closest("[data-debater]");
    const schoolSelect = debaterContainer
      ? debaterContainer.querySelector("[data-school-select]")
      : null;

    if (
      !schoolSelect ||
      !schoolSelect.value ||
      !schoolSelect.value.startsWith("apda:")
    ) {
      field.innerHTML = '<option value="">Select a school first</option>';
      field.disabled = true;
    } else {
      // If school is already selected, sync the debater data
      syncDebater(field);
    }
  });
  if (type === "team") {
    ensureSeedSelectState(element);
  }
};
export default function initRegistrationPortal() {
  root = document.getElementById("registration-app");
  if (!root) {
    return;
  }
  const maxTeams = parseInt(root.dataset.maxTeams || "200", 10);
  initCollapsibles();

  // Initialize existing school selects
  queryAll("[data-school-select]").forEach((selectEl) => {
    const select = selectEl;
    toggleNew(select);
    updateDebaters(select);
  });

  // Initialize existing debater selects - disable if no school selected
  queryAll("[data-debater-input]").forEach((fieldEl) => {
    const field = fieldEl;
    const schoolSelectId = field
      .closest("[data-debater]")
      ?.querySelector("[data-school-select]")?.id;
    if (schoolSelectId) {
      const schoolSelect = byId(schoolSelectId);
      if (
        !schoolSelect ||
        !schoolSelect.value ||
        !schoolSelect.value.startsWith("apda:")
      ) {
        field.innerHTML = '<option value="">Select a school first</option>';
        field.disabled = true;
      }
    }
  });
  queryAll('[data-form="team"]').forEach((teamForm) => {
    ensureSeedSelectState(teamForm);
  });

  // Enable/disable Add Team button based on main school selection
  const updateAddTeamButton = () => {
    const mainSchoolSelect = root.querySelector(
      '[data-school-select="registration"]',
    );
    const addTeamButton = root.querySelector('[data-add-form="team"]');
    if (addTeamButton) {
      if (
        mainSchoolSelect &&
        mainSchoolSelect.value &&
        mainSchoolSelect.value !== ""
      ) {
        addTeamButton.disabled = false;
      } else {
        addTeamButton.disabled = true;
      }
    }
  };

  updateAddTeamButton();

  root.addEventListener("change", (event) => {
    const { target } = event;

    // Handle debater select change
    if (target.matches("[data-debater-input]")) {
      syncDebater(target);
      return;
    }

    if (target.matches("[data-school-select]")) {
      toggleNew(target);
      updateDebaters(target);

      // Update the Add Team button and propagate the selection when the main
      // school dropdown changes.
      if (target.matches('[data-school-select="registration"]')) {
        updateAddTeamButton();

        // Propagate main school options and value to every team school select
        queryAll('[data-form="team"]').forEach((teamForm) => {
          queryAll("[data-school-select]", teamForm).forEach((selectEl) => {
            const schoolSelect = selectEl;
            schoolSelect.innerHTML = "";
            queryAll("option", target).forEach((option) => {
              const newOption = option.cloneNode(true);
              schoolSelect.appendChild(newOption);
            });

            if (
              (!schoolSelect.value || schoolSelect.value === "") &&
              target.value &&
              target.value !== NEW &&
              target.value !== ""
            ) {
              schoolSelect.value = target.value;
              toggleNew(schoolSelect);
              if (schoolSelect.dataset.listId) {
                updateDebaters(schoolSelect);
              }
            }
          });
        });
      }
    }
    if (target.matches("[data-team-seed]")) {
      target.dataset.autoset = "false";
    }
  });
  root.addEventListener(
    "input",
    (event) =>
      event.target.matches("[data-debater-input]") && syncDebater(event.target),
  );
  root.addEventListener("click", (event) => {
    // Handle "Create New School" button
    if (event.target.matches("[data-trigger-new]")) {
      event.preventDefault();
      const selectId = event.target.getAttribute("data-trigger-new");
      const select = document.getElementById(selectId);
      if (select) {
        select.value = NEW;
        toggleNew(select);
      }
      return;
    }

    const addType = event.target.getAttribute("data-add-form");
    if (addType) {
      event.preventDefault();
      addForm(addType, maxTeams);
      return;
    }
    const removeType = event.target.getAttribute("data-remove-form");
    if (!removeType) {
      return;
    }
    event.preventDefault();
    const form = event.target.closest(`[data-form="${removeType}"]`);
    if (!form) {
      return;
    }
    const deleteField = form.querySelector('input[name$="-DELETE"]');
    if (deleteField) {
      deleteField.value = "on";
    }
    form.classList.add("d-none");
  });

  // Store custom schools and debaters globally for reuse inside
  // updateDebaters.
  window.customSchools = [];
  window.customDebaters = {};

  // Create new school handler
  const createSchoolBtn = byId("create-school-btn");
  const newSchoolNameInput = byId("new-school-name");

  if (createSchoolBtn && newSchoolNameInput) {
    createSchoolBtn.addEventListener("click", () => {
      const schoolName = newSchoolNameInput.value.trim();
      if (!schoolName) {
        alert("Please enter a school name");
        return;
      }

      // Create a custom school with id = -1 (or negative incrementing IDs)
      const customId = -1 - window.customSchools.length;
      const schoolValue = `custom:${customId}`;
      const school = {
        id: customId,
        name: schoolName,
        value: schoolValue,
      };

      window.customSchools.push(school);

      // Add to all school dropdowns
      queryAll("[data-school-select]").forEach((selectEl) => {
        const select = selectEl;
        const option = document.createElement("option");
        option.value = schoolValue;
        option.textContent = schoolName;
        // Store name for form submission
        option.dataset.schoolName = schoolName;
        select.appendChild(option);
      });

      // Clear the input
      newSchoolNameInput.value = "";

      // Show success message
      alert(`School "${schoolName}" added successfully!`);
    });
  }

  // Create new debater handler
  const createDebaterBtn = byId("create-debater-btn");
  const newDebaterSchool = byId("new-debater-school");
  const newDebaterName = byId("new-debater-name");
  const newDebaterStatus = byId("new-debater-status");

  if (
    createDebaterBtn &&
    newDebaterSchool &&
    newDebaterName &&
    newDebaterStatus
  ) {
    createDebaterBtn.addEventListener("click", () => {
      const schoolValue = newDebaterSchool.value;
      const customDebaterName = newDebaterName.value.trim();
      const noviceStatus = newDebaterStatus.value;

      if (!schoolValue) {
        alert("Please select a school");
        return;
      }
      if (!customDebaterName) {
        alert("Please enter a debater name");
        return;
      }

      // Ensure the school selection is valid before proceeding
      const [schoolPrefix, schoolIdValue] = schoolValue.split(":");
      if (!schoolIdValue || !["apda", "custom", "pk"].includes(schoolPrefix)) {
        alert("Invalid school selection");
        return;
      }

      // Create custom debater with id = -1
      const customId = -1;
      const debater = {
        id: customId,
        apda_id: customId,
        name: customDebaterName,
        full_name: customDebaterName,
        status: noviceStatus === "1" ? "Novice" : "Varsity",
        qualified: false,
      };

      // Store debater for this school
      if (!window.customDebaters[schoolValue]) {
        window.customDebaters[schoolValue] = [];
      }
      window.customDebaters[schoolValue].push(debater);

      // Add to any active debater dropdowns for this school
      queryAll("[data-school-select]").forEach((selectEl) => {
        const schoolSelect = selectEl;
        if (schoolSelect.value === schoolValue) {
          // Find associated debater select
          const { nameId } = schoolSelect.dataset;
          if (nameId) {
            const debaterSelect = byId(nameId);
            if (debaterSelect) {
              const option = document.createElement("option");
              option.value = `custom:${customId}`;
              option.textContent = customDebaterName;
              option.dataset.debater = JSON.stringify(debater);
              debaterSelect.appendChild(option);

              // Enable the select if it was disabled
              if (debaterSelect.disabled) {
                debaterSelect.disabled = false;
                debaterSelect.innerHTML =
                  '<option value="">Select debater</option>';
                debaterSelect.appendChild(option);
              }
            }
          }
        }
      });

      // Clear the inputs
      newDebaterName.value = "";
      newDebaterStatus.value = "0";

      // Show success message
      alert(`Debater "${customDebaterName}" added successfully!`);
    });
  }

  // Populate new-debater-school with schools from main dropdown on load
  const mainSchoolSelect = queryAll('[data-school-select="registration"]')[0];
  if (mainSchoolSelect && newDebaterSchool) {
    const syncNewDebaterSchools = () => {
      const currentValue = newDebaterSchool.value;
      newDebaterSchool.innerHTML = '<option value="">Select school</option>';

      queryAll("option", mainSchoolSelect).forEach((opt) => {
        if (opt.value && opt.value !== "") {
          const option = document.createElement("option");
          option.value = opt.value;
          option.textContent = opt.textContent;
          newDebaterSchool.appendChild(option);
        }
      });

      // Restore value if still available
      if (currentValue) {
        newDebaterSchool.value = currentValue;
      }
    };

    // Sync on load
    syncNewDebaterSchools();

    // Re-sync when schools are added
    const observer = new MutationObserver(syncNewDebaterSchools);
    observer.observe(mainSchoolSelect, { childList: true });
  }
}
