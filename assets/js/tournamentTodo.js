import "../css/tournament-todo.scss";

function initializeTournamentTodoAutosave() {
  const updateSectionProgressDisplay = (section, completed, total, percent) => {
    if (!section) {
      return;
    }

    const progressText = section.querySelector(".todo-section-progress-text");
    if (progressText) {
      progressText.dataset.completed = String(completed);
      progressText.dataset.total = String(total);
      progressText.textContent = `${completed} / ${total}`;
    }

    const progressBar = section.querySelector(".todo-section-progress-bar");
    if (progressBar) {
      progressBar.dataset.progressPercent = String(percent);
      progressBar.style.width = `${percent}%`;
    }
  };

  const updateSectionProgressFromDom = (section) => {
    if (!section) {
      return;
    }

    const checkboxes = section.querySelectorAll(".todo-step-checkbox");
    const total = checkboxes.length;
    const completed = Array.from(checkboxes).filter(
      (checkbox) => checkbox.checked,
    ).length;
    const percent = total ? Math.round((completed / total) * 100) : 0;

    updateSectionProgressDisplay(section, completed, total, percent);
  };

  const updateAllSectionProgressFromDom = (form) => {
    form.querySelectorAll(".todo-section").forEach((section) => {
      updateSectionProgressFromDom(section);
    });
  };

  const setRowCompleteClass = (input, isChecked) => {
    const row = input.closest(".todo-step-item");
    if (row) {
      row.classList.toggle("is-complete", !!isChecked);
    }
  };

  const form = document.querySelector(".todo-checklist-form");
  if (!form) {
    return;
  }

  const { autosaveUrl } = form.dataset;
  if (!autosaveUrl) {
    return;
  }

  const csrfInput = form.querySelector("input[name='csrfmiddlewaretoken']");
  if (!csrfInput) {
    return;
  }

  const csrfToken = csrfInput.value;
  const inputs = form.querySelectorAll(
    ".todo-step-checkbox, .todo-preference-checkbox",
  );
  updateAllSectionProgressFromDom(form);

  inputs.forEach((input) => {
    input.addEventListener("change", (event) => {
      const checkbox = event.currentTarget;
      if (!(checkbox instanceof HTMLInputElement)) {
        return;
      }

      const originalDisabled = checkbox.disabled;
      const nextChecked = checkbox.checked;
      const section = checkbox.closest(".todo-section");

      checkbox.disabled = true;
      setRowCompleteClass(checkbox, nextChecked);
      updateSectionProgressFromDom(section);
      const body = new URLSearchParams({
        csrfmiddlewaretoken: csrfToken,
        field: checkbox.name,
        checked: nextChecked ? "1" : "0",
      });

      fetch(autosaveUrl, {
        method: "POST",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": csrfToken,
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        },
        credentials: "same-origin",
        body,
      })
        .then((response) => {
          if (!response.ok) {
            throw new Error("Failed to save checklist item");
          }
          return response.json();
        })
        .then((data) => {
          checkbox.checked = !!data.checked;
          setRowCompleteClass(checkbox, checkbox.checked);
          checkbox.disabled = originalDisabled || !!data.auto_completed;

          if (data.phase) {
            const serverSection = form.querySelector(
              `.todo-section[data-phase="${data.phase}"]`,
            );
            if (
              serverSection &&
              typeof data.section_completed === "number" &&
              typeof data.section_total === "number" &&
              typeof data.section_progress_percent === "number"
            ) {
              updateSectionProgressDisplay(
                serverSection,
                data.section_completed,
                data.section_total,
                data.section_progress_percent,
              );
            } else {
              updateSectionProgressFromDom(section);
            }
          } else {
            updateSectionProgressFromDom(section);
          }
        })
        .catch(() => {
          checkbox.checked = !nextChecked;
          setRowCompleteClass(checkbox, checkbox.checked);
          checkbox.disabled = originalDisabled;
          updateSectionProgressFromDom(section);
        });
    });
  });
}

if (document.readyState === "loading") {
  document.addEventListener(
    "DOMContentLoaded",
    initializeTournamentTodoAutosave,
  );
} else {
  initializeTournamentTodoAutosave();
}
