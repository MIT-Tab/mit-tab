const NEW = "__new__";
// Use our proxy endpoints to avoid CORS issues
const DEB_URL = id => `/registration/api/debaters/${id}/`;
const SCHOOLS_ALL_URL = "/registration/api/schools/all/";
let root;
let allSchoolsLoaded = false;

const queryAll = (selector, scope = root) =>
  Array.from(scope.querySelectorAll(selector));
const byId = value => document.getElementById(value);
const debaterName = person =>
  person.name ||
  person.full_name ||
  `${person.first_name || ""} ${person.last_name || ""}`.trim();

const toggleNew = select => {
  queryAll(`[data-new-school-container="${select.id}"]`).forEach(container => {
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
  });
};

const loadAllSchools = () => {
  if (allSchoolsLoaded) {
    return Promise.resolve();
  }
  
  // Update the "See more" link to show loading state
  queryAll('[data-load-all-schools]').forEach(link => {
    link.textContent = 'Loading...';
  });
  
  return fetch(SCHOOLS_ALL_URL)
    .then(response => {
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      return response.json();
    })
    .then(data => {
      const schools = Array.isArray(data) ? data : data.schools || [];
      const schoolSelects = queryAll('[data-school-select]');
      
      schoolSelects.forEach(select => {
        const currentValue = select.value;
        const existingValues = new Set();
        
        // Collect existing school IDs
        queryAll('option', select).forEach(option => {
          if (option.value && option.value.startsWith('apda:')) {
            existingValues.add(option.value);
          }
        });
        
        // Add new schools
        const newSchoolOption = select.querySelector(`option[value="${NEW}"]`);
        schools.forEach(school => {
          const value = `apda:${school.id || school.apda_id}`;
          if (!existingValues.has(value)) {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = school.name;
            if (newSchoolOption) {
              select.insertBefore(option, newSchoolOption);
            } else {
              select.appendChild(option);
            }
          }
        });
        
        // Restore selected value
        select.value = currentValue;
      });
      
      allSchoolsLoaded = true;
      
      // Update the "See more" link
      queryAll('[data-load-all-schools]').forEach(link => {
        link.textContent = 'All schools loaded';
        link.style.pointerEvents = 'none';
        link.style.color = '#6c757d';
      });
    })
    .catch(error => {
      console.error('Failed to load all schools:', error);
      // Update the "See more" link to show error and allow retry
      queryAll('[data-load-all-schools]').forEach(link => {
        link.textContent = 'Failed to load. Click to retry.';
        link.style.color = '#dc3545';
      });
      allSchoolsLoaded = false; // Allow retry
    });
};
const syncDebater = input => {
  const container = input.closest('[data-debater]');
  if (!container) return;
  
  const prefix = input.dataset.debaterInput;
  if (!prefix) return;
  
  // Find the matching option in the select itself
  const selectedOption = input.selectedOptions && input.selectedOptions[0];
  
  if (selectedOption && selectedOption.dataset.debater) {
    // Parse the debater data stored in the option
    try {
      const debaterData = JSON.parse(selectedOption.dataset.debater);
      
      // Populate all the hidden fields
      const apdaIdField = byId(input.dataset.apdaTarget);
      const noviceField = container.querySelector(`input[name$="${prefix}_novice_status"]`);
      const qualifiedField = container.querySelector(`input[name$="${prefix}_qualified"]`);
      
      if (apdaIdField) {
        apdaIdField.value = debaterData.apda_id || debaterData.id || '';
      }
      if (noviceField) {
        // Convert status to novice value: "Novice" = 1, anything else = 0
        noviceField.value = debaterData.status === 'Novice' ? '1' : '0';
      }
      if (qualifiedField) {
        // Assume qualified if they have an APDA ID
        qualifiedField.value = debaterData.apda_id ? 'on' : '';
      }
    } catch (e) {
      console.error('Error parsing debater data:', e);
    }
  } else {
    // Clear the fields if no match
    const apdaIdField = byId(input.dataset.apdaTarget);
    const noviceField = container.querySelector(`input[name$="${prefix}_novice_status"]`);
    const qualifiedField = container.querySelector(`input[name$="${prefix}_qualified"]`);
    
    if (apdaIdField) apdaIdField.value = '';
    if (noviceField) noviceField.value = '0';
    if (qualifiedField) qualifiedField.value = '';
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

const updateDebaters = select => {
  const listId = select.dataset.listId;
  const nameId = select.dataset.nameId;
  
  console.log('updateDebaters called', { listId, nameId, selectValue: select.value });
  
  // Get or create the datalist element
  let list = byId(listId);
  if (!list) {
    console.log('Creating datalist with id:', listId);
    list = document.createElement('datalist');
    list.id = listId;
    list.style.display = 'none';
    document.body.appendChild(list);
  }
  
  const selectElement = byId(nameId);
  
  console.log('Found elements:', { list: !!list, selectElement: !!selectElement });
  
  if (!selectElement) {
    console.log('Missing select element');
    return;
  }
  
  // Handle custom schools - they only have manually created debaters
  if (select.value && select.value.startsWith('custom:')) {
    const selectedOption = select.selectedOptions && select.selectedOptions[0];
    if (selectedOption) {
      const schoolName = selectedOption.textContent;
      // Find the associated school_name hidden field
      const schoolNameField = select.closest('[data-debater]')?.querySelector(`input[name$="_school_name"]`);
      if (schoolNameField) {
        schoolNameField.value = schoolName;
      }
    }
    
    // Clear and add any custom debaters for this school
    selectElement.innerHTML = '<option value="">Select a debater</option>';
    const schoolValue = select.value;
    if (window.customDebaters && window.customDebaters[schoolValue]) {
      window.customDebaters[schoolValue].forEach(debater => {
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
    .then(response => (response.ok ? response.json() : { debaters: [] }))
    .then(data => {
      const entries = Array.isArray(data) ? data : data.debaters || [];
      
      console.log('Loaded debaters:', entries.length);
      
      // Clear the select and add options
      selectElement.innerHTML = "";
      
      // Add an empty option first
      const emptyOption = document.createElement("option");
      emptyOption.value = "";
      emptyOption.textContent = "Select a debater";
      selectElement.appendChild(emptyOption);
      
      // Add debater options from API
      entries.forEach(person => {
        const option = document.createElement("option");
        const name = debaterName(person);
        option.value = name;
        option.textContent = name;
        
        // Store the full debater data in the option
        option.dataset.debater = JSON.stringify({
          id: person.id || person.apda_id,
          apda_id: person.apda_id || person.id,
          name: name,
          first_name: person.first_name,
          last_name: person.last_name,
          status: person.status
        });
        
        selectElement.appendChild(option);
      });
      
      // Add custom debaters for this school if any exist
      const schoolValue = select.value;
      if (window.customDebaters && window.customDebaters[schoolValue]) {
        window.customDebaters[schoolValue].forEach(debater => {
          const option = document.createElement("option");
          option.value = `custom:${debater.id}`;
          option.textContent = debater.name;
          option.dataset.debater = JSON.stringify(debater);
          selectElement.appendChild(option);
        });
      }
      
      // Also store in the datalist for reference (though we don't use it visually)
      list.innerHTML = "";
      entries.forEach(person => {
        const option = document.createElement("option");
        const name = debaterName(person);
        option.value = name;
        option.dataset.debater = JSON.stringify({
          id: person.id || person.apda_id,
          apda_id: person.apda_id || person.id,
          name: name,
          first_name: person.first_name,
          last_name: person.last_name,
          status: person.status
        });
        list.appendChild(option);
      });
      
      // Enable the select
      selectElement.disabled = false;
      
      console.log('Debaters loaded successfully');
      
      // Trigger sync to populate hidden fields if there's a pre-selected value
      syncDebater(selectElement);
    })
    .catch((error) => {
      console.error('Error loading debaters:', error);
      clearDebaterList(list, selectElement);
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
  queryAll('[data-name-id*="__prefix__"]', element).forEach(el => {
    el.dataset.nameId = el.dataset.nameId.replace(/__prefix__/g, String(total));
  });
  queryAll('[data-list-id*="__prefix__"]', element).forEach(el => {
    el.dataset.listId = el.dataset.listId.replace(/__prefix__/g, String(total));
  });
  
  root.querySelector(`[data-formset-container="${type}"]`).appendChild(element);
  totalInput.value = total + 1;
  
  // If it's a team form, populate ALL school selects (including debater schools) from main registration school
  if (type === "team") {
    const mainSchoolSelect = root.querySelector('[data-school-select="registration"]');
    
    // First, copy all options from main school select to all new school selects in the team form
    const allSchoolSelects = queryAll('[data-school-select]', element);
    allSchoolSelects.forEach(select => {
      // Copy options from main school select
      if (mainSchoolSelect) {
        // Clear existing options first
        select.innerHTML = '';
        
        // Clone all options from main school select
        queryAll('option', mainSchoolSelect).forEach(option => {
          const newOption = option.cloneNode(true);
          select.appendChild(newOption);
        });
      }
      
      // Now set the value if main school is selected
      if (mainSchoolSelect && mainSchoolSelect.value && mainSchoolSelect.value !== NEW && mainSchoolSelect.value !== "") {
        select.value = mainSchoolSelect.value;
        toggleNew(select);
        // Only update debaters if it's a team school select (has listId)
        if (select.dataset.listId) {
          updateDebaters(select);
        }
      } else {
        toggleNew(select);
        const listId = select.dataset.listId;
        const nameId = select.dataset.nameId;
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
  
  queryAll("[data-debater-input]", element).forEach(field => {
    // Debater fields should now be select elements
    const debaterContainer = field.closest('[data-debater]');
    const schoolSelect = debaterContainer ? debaterContainer.querySelector('[data-school-select]') : null;
    
    if (!schoolSelect || !schoolSelect.value || !schoolSelect.value.startsWith("apda:")) {
      field.innerHTML = '<option value="">Select a school first</option>';
      field.disabled = true;
    } else {
      // If school is already selected, sync the debater data
      syncDebater(field);
    }
  });
};
export default function initRegistrationPortal() {
  root = document.getElementById("registration-app");
  if (!root) {
    return;
  }
  const maxTeams = parseInt(root.dataset.maxTeams || "200", 10);
  
  // Initialize existing school selects
  queryAll("[data-school-select]").forEach(select => {
    toggleNew(select);
    updateDebaters(select);
  });
  
  // Initialize existing debater selects - disable if no school selected
  queryAll("[data-debater-input]").forEach(field => {
    const schoolSelectId = field.closest('[data-debater]')?.querySelector('[data-school-select]')?.id;
    if (schoolSelectId) {
      const schoolSelect = byId(schoolSelectId);
      if (!schoolSelect || !schoolSelect.value || !schoolSelect.value.startsWith("apda:")) {
        field.innerHTML = '<option value="">Select a school first</option>';
        field.disabled = true;
      }
    }
  });
  
  // Enable/disable Add Team button based on main school selection
  const updateAddTeamButton = () => {
    const mainSchoolSelect = root.querySelector('[data-school-select="registration"]');
    const addTeamButton = root.querySelector('[data-add-form="team"]');
    if (addTeamButton) {
      if (mainSchoolSelect && mainSchoolSelect.value && mainSchoolSelect.value !== "") {
        addTeamButton.disabled = false;
      } else {
        addTeamButton.disabled = true;
      }
    }
  };
  
  updateAddTeamButton();
  
  root.addEventListener("change", event => {
    const { target } = event;
    
    // Handle debater select change
    if (target.matches("[data-debater-input]")) {
      syncDebater(target);
      return;
    }
    
    if (target.matches("[data-school-select]")) {
      toggleNew(target);
      updateDebaters(target);
      
      // Update Add Team button and propagate school to all teams when main school changes
      if (target.matches('[data-school-select="registration"]')) {
        updateAddTeamButton();
        
        // Propagate main school options and value to all team school selects
        queryAll('[data-form="team"]').forEach(teamForm => {
          queryAll('[data-school-select]', teamForm).forEach(schoolSelect => {
            // First, sync the options from main school select
            schoolSelect.innerHTML = '';
            queryAll('option', target).forEach(option => {
              const newOption = option.cloneNode(true);
              schoolSelect.appendChild(newOption);
            });
            
            // Then update value if the school select doesn't already have a different value
            if (!schoolSelect.value || schoolSelect.value === "") {
              if (target.value && target.value !== NEW && target.value !== "") {
                schoolSelect.value = target.value;
                toggleNew(schoolSelect);
                if (schoolSelect.dataset.listId) {
                  updateDebaters(schoolSelect);
                }
              }
            }
          });
        });
      }
    }
  });
  root.addEventListener(
    "input",
    event =>
      event.target.matches("[data-debater-input]") && syncDebater(event.target)
  );
  root.addEventListener("click", event => {
    // Handle "See more schools" link
    if (event.target.matches('[data-load-all-schools]')) {
      event.preventDefault();
      loadAllSchools();
      return;
    }
    
    // Handle "Create New School" button
    if (event.target.matches('[data-trigger-new]')) {
      event.preventDefault();
      const selectId = event.target.getAttribute('data-trigger-new');
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

  // Store for custom schools and debaters (make global for access in updateDebaters)
  window.customSchools = [];
  window.customDebaters = {};

  // Create new school handler
  const createSchoolBtn = byId('create-school-btn');
  const newSchoolNameInput = byId('new-school-name');
  
  if (createSchoolBtn && newSchoolNameInput) {
    createSchoolBtn.addEventListener('click', () => {
      const schoolName = newSchoolNameInput.value.trim();
      if (!schoolName) {
        alert('Please enter a school name');
        return;
      }
      
      // Create a custom school with id = -1 (or negative incrementing IDs)
      const customId = -1 - window.customSchools.length;
      const schoolValue = `custom:${customId}`;
      const school = {
        id: customId,
        name: schoolName,
        value: schoolValue
      };
      
      window.customSchools.push(school);
      
      // Add to all school dropdowns
      queryAll('[data-school-select]').forEach(select => {
        const option = document.createElement('option');
        option.value = schoolValue;
        option.textContent = schoolName;
        option.dataset.schoolName = schoolName; // Store name for form submission
        select.appendChild(option);
      });
      
      // Clear the input
      newSchoolNameInput.value = '';
      
      // Show success message
      alert(`School "${schoolName}" added successfully!`);
    });
  }

  // Create new debater handler
  const createDebaterBtn = byId('create-debater-btn');
  const newDebaterSchool = byId('new-debater-school');
  const newDebaterName = byId('new-debater-name');
  const newDebaterStatus = byId('new-debater-status');
  
  if (createDebaterBtn && newDebaterSchool && newDebaterName && newDebaterStatus) {
    createDebaterBtn.addEventListener('click', () => {
      const schoolValue = newDebaterSchool.value;
      const debaterName = newDebaterName.value.trim();
      const noviceStatus = newDebaterStatus.value;
      
      if (!schoolValue) {
        alert('Please select a school');
        return;
      }
      if (!debaterName) {
        alert('Please enter a debater name');
        return;
      }
      
      // Extract school ID
      let schoolId;
      if (schoolValue.startsWith('apda:')) {
        schoolId = schoolValue.split(':')[1];
      } else if (schoolValue.startsWith('custom:')) {
        schoolId = schoolValue.split(':')[1];
      } else if (schoolValue.startsWith('pk:')) {
        schoolId = schoolValue.split(':')[1];
      } else {
        alert('Invalid school selection');
        return;
      }
      
      // Create custom debater with id = -1
      const customId = -1;
      const debater = {
        id: customId,
        apda_id: customId,
        name: debaterName,
        full_name: debaterName,
        status: noviceStatus === '1' ? 'Novice' : 'Varsity',
        qualified: false
      };
      
      // Store debater for this school
      if (!window.customDebaters[schoolValue]) {
        window.customDebaters[schoolValue] = [];
      }
      window.customDebaters[schoolValue].push(debater);
      
      // Add to any active debater dropdowns for this school
      queryAll('[data-school-select]').forEach(schoolSelect => {
        if (schoolSelect.value === schoolValue) {
          // Find associated debater select
          const nameId = schoolSelect.dataset.nameId;
          if (nameId) {
            const debaterSelect = byId(nameId);
            if (debaterSelect) {
              const option = document.createElement('option');
              option.value = `custom:${customId}`;
              option.textContent = debaterName;
              option.dataset.debater = JSON.stringify(debater);
              debaterSelect.appendChild(option);
              
              // Enable the select if it was disabled
              if (debaterSelect.disabled) {
                debaterSelect.disabled = false;
                debaterSelect.innerHTML = '<option value="">Select debater</option>';
                debaterSelect.appendChild(option);
              }
            }
          }
        }
      });
      
      // Clear the inputs
      newDebaterName.value = '';
      newDebaterStatus.value = '0';
      
      // Show success message
      alert(`Debater "${debaterName}" added successfully!`);
    });
  }

  // Populate new-debater-school with schools from main dropdown on load
  const mainSchoolSelect = queryAll('[data-school-select="registration"]')[0];
  if (mainSchoolSelect && newDebaterSchool) {
    const syncNewDebaterSchools = () => {
      const currentValue = newDebaterSchool.value;
      newDebaterSchool.innerHTML = '<option value="">Select school</option>';
      
      queryAll('option', mainSchoolSelect).forEach(opt => {
        if (opt.value && opt.value !== '') {
          const option = document.createElement('option');
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

