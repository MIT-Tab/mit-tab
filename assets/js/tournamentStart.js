function initializeCustomStaffRoles() {
  const rows = document.querySelector("#custom-staff-rows");
  const template = document.querySelector("#custom-staff-empty-form");
  const totalForms = document.querySelector("#id_custom_staff-TOTAL_FORMS");
  const addButton = document.querySelector("#add-custom-staff");

  if (!rows || !template || !totalForms || !addButton) {
    return;
  }

  addButton.addEventListener("click", () => {
    const index = Number.parseInt(totalForms.value, 10);
    rows.insertAdjacentHTML(
      "beforeend",
      template.innerHTML.replaceAll("__prefix__", String(index)),
    );
    totalForms.value = String(index + 1);
  });

  rows.addEventListener("click", (event) => {
    const removeButton = event.target.closest(".js-remove-custom-staff");
    if (!removeButton) {
      return;
    }
    const row = removeButton.closest(".custom-staff-row");
    const deleteInput = row?.querySelector("input[name$='-DELETE']");
    if (row && deleteInput) {
      deleteInput.value = "on";
      row.hidden = true;
    }
  });
}

function inviteErrorMessage(error) {
  if (typeof error === "string") {
    return error;
  }
  return error?.message || "Check this field and try again.";
}

function initializeStaffInvites() {
  const form = document.querySelector("#staff-invite-form");
  const status = document.querySelector("#staff-invite-status");
  if (!form || !status) {
    return;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submitButton = form.querySelector("button[type='submit']");
    const originalText = submitButton.textContent;
    submitButton.disabled = true;
    submitButton.textContent = "Sending…";

    form.querySelectorAll(".is-invalid").forEach((field) => {
      field.classList.remove("is-invalid");
    });
    form.querySelectorAll("[data-invite-errors-for]").forEach((element) => {
      element.replaceChildren();
    });

    try {
      const response = await fetch(form.action || window.location.href, {
        method: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
        body: new FormData(form),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        Object.entries(data.errors || {}).forEach(([fieldName, errors]) => {
          const errorContainer = form.querySelector(
            `[data-invite-errors-for="${fieldName}"]`,
          );
          const field = form.querySelector(`[name="invite-${fieldName}"]`);
          if (field) {
            field.classList.add("is-invalid");
          }
          if (errorContainer) {
            errorContainer.textContent = errors
              .map(inviteErrorMessage)
              .join(" ");
          }
        });
        throw new Error(data.message || "Could not send the invite.");
      }

      status.className = "alert alert-success py-2";
      status.textContent = data.message;
      form.reset();
    } catch (error) {
      if (!form.querySelector("[data-invite-errors-for]:not(:empty)")) {
        status.className = "alert alert-danger py-2";
        status.textContent = error.message;
      }
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = originalText;
    }
  });
}

function initializeTournamentStart() {
  initializeCustomStaffRoles();
  initializeStaffInvites();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeTournamentStart);
} else {
  initializeTournamentStart();
}
