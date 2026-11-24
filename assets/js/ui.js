import initializeDropdowns from "./dropdowns";

const targetFor = (id) => document.getElementById(id);
const collapseSelector = "[data-ui-collapse]";
const tabSelector = "[data-ui-tab]";

function setExpanded(trigger, expanded) {
  const panel = targetFor(trigger.dataset.uiCollapse);
  panel.classList.toggle("show", expanded);
  trigger.setAttribute("aria-expanded", String(expanded));
}

function initializeDisclosures() {
  const accordion = window.matchMedia("(max-width: 767.98px)");
  const indexTriggers = document.querySelectorAll(
    `.index_container ${collapseSelector}`,
  );
  if (accordion.matches) {
    indexTriggers.forEach((trigger) => setExpanded(trigger, false));
  }

  document.querySelectorAll(collapseSelector).forEach((trigger) => {
    const panel = targetFor(trigger.dataset.uiCollapse);
    if (!panel) return;
    trigger.setAttribute("aria-controls", panel.id);
    trigger.setAttribute(
      "aria-expanded",
      String(panel.classList.contains("show")),
    );
    trigger.addEventListener("click", () => {
      const expanded = !panel.classList.contains("show");
      if (
        expanded &&
        accordion.matches &&
        trigger.closest(".index_container")
      ) {
        indexTriggers.forEach((other) => setExpanded(other, false));
      }
      setExpanded(trigger, expanded);
    });
  });
}

function initializeTabs() {
  document.querySelectorAll('[role="tablist"]').forEach((list) => {
    const tabs = Array.from(list.querySelectorAll(tabSelector));
    const select = (selected) => {
      tabs.forEach((tab) => {
        const panel = targetFor(tab.hash.slice(1));
        const active = selected === tab;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", String(active));
        tab.setAttribute("tabindex", active ? "0" : "-1");
        panel.classList.toggle("active", active);
      });
      selected.dispatchEvent(new Event("mittab:tab-shown", { bubbles: true }));
    };

    tabs.forEach((tab, index) => {
      const panel = targetFor(tab.hash.slice(1));
      tab.setAttribute("aria-controls", panel.id);
      tab.setAttribute("role", "tab");
      panel.setAttribute("role", "tabpanel");
      panel.setAttribute("aria-labelledby", tab.id);
      tab.addEventListener("click", (event) => {
        event.preventDefault();
        select(tab);
      });
      tab.addEventListener("keydown", (event) => {
        const destinations = {
          ArrowLeft: (index + tabs.length - 1) % tabs.length,
          ArrowRight: (index + 1) % tabs.length,
          Home: 0,
          End: tabs.length - 1,
          " ": index,
        };
        const next = tabs[destinations[event.key]];
        if (!next) return;
        event.preventDefault();
        select(next);
        next.focus();
      });
    });

    const selected = tabs.find((tab) => tab.classList.contains("active"));
    if (selected) select(selected);
  });
}

function initializeDialogs() {
  document.querySelectorAll("dialog.modal").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (
        event.target === dialog ||
        event.target.closest("[data-ui-dialog-close]")
      ) {
        dialog.close();
      }
    });
    if (dialog.hasAttribute("data-ui-dialog-autostart")) dialog.showModal();
  });

  document.querySelectorAll("[data-ui-dialog]").forEach((trigger) => {
    trigger.addEventListener("click", () => {
      targetFor(trigger.dataset.uiDialog).showModal();
    });
  });
}

function initializeTooltips() {
  document.querySelectorAll("[data-ui-tooltip]").forEach((trigger, index) => {
    const tooltip = document.createElement("span");
    tooltip.setAttribute("role", "tooltip");
    tooltip.id = `ui-tooltip-${index}`;
    tooltip.className = "ui-tooltip";
    tooltip.hidden = true;
    tooltip.textContent = trigger.title;
    trigger.removeAttribute("title");
    trigger.setAttribute("aria-describedby", tooltip.id);
    document.body.append(tooltip);

    const hide = () => {
      tooltip.hidden = true;
    };
    const show = () => {
      const anchor = trigger.getBoundingClientRect();
      tooltip.hidden = false;
      const left = Math.max(
        8,
        Math.min(
          anchor.left + (anchor.width - tooltip.offsetWidth) / 2,
          document.documentElement.clientWidth - tooltip.offsetWidth - 8,
        ),
      );
      const below =
        trigger.dataset.uiTooltip === "bottom" ||
        anchor.top < tooltip.offsetHeight + 8;
      tooltip.style.left = `${left}px`;
      tooltip.style.top = below
        ? `${anchor.bottom + 5}px`
        : `${anchor.top - tooltip.offsetHeight - 5}px`;
    };

    trigger.addEventListener("mouseenter", show);
    trigger.addEventListener("focus", show);
    trigger.addEventListener("click", show);
    trigger.addEventListener("blur", hide);
    trigger.addEventListener("mouseleave", () => {
      if (document.activeElement !== trigger) hide();
    });
    trigger.addEventListener("keydown", (event) => {
      if (event.key === "Escape") hide();
    });
    document.addEventListener(
      "click",
      (event) => {
        if (!trigger.contains(event.target)) hide();
      },
      true,
    );
    document.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initializeDropdowns();
  initializeDisclosures();
  initializeTabs();
  initializeDialogs();
  initializeTooltips();
});
