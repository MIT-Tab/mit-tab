const selector = "[data-ui-dropdown]";
const mobileNav = window.matchMedia("(max-width: 991.98px)");
let active = null;

function positionMenu() {
  if (!active) return;
  const { trigger, menu } = active;
  if (trigger.closest(".mittab-nav") && mobileNav.matches) return;

  const anchor = trigger.getBoundingClientRect();
  const width = document.documentElement.clientWidth;
  const height = document.documentElement.clientHeight;
  const spaceBelow = height - anchor.bottom - 8;
  const spaceAbove = anchor.top - 8;
  const preferredHeight = Math.min(menu.scrollHeight + 2, 400);
  const openUp = spaceBelow < preferredHeight && spaceAbove > spaceBelow;
  const menuHeight = Math.max(0, openUp ? spaceAbove : spaceBelow);
  menu.style.setProperty("--menu-height", `${Math.min(400, menuHeight)}px`);

  const start = menu.classList.contains("dropdown-menu-right")
    ? anchor.right - menu.offsetWidth
    : anchor.left;
  const left = Math.max(8, Math.min(start, width - menu.offsetWidth - 8));
  const top = openUp ? anchor.top - menu.offsetHeight - 3 : anchor.bottom + 3;
  menu.style.setProperty("--menu-left", `${left}px`);
  menu.style.setProperty("--menu-top", `${top}px`);
}

const resize = new ResizeObserver(positionMenu);

function closeMenu(restoreFocus = false) {
  if (!active) return;
  const { trigger, menu } = active;
  active = null;
  resize.disconnect();
  trigger.setAttribute("aria-expanded", "false");
  menu.classList.remove("show");
  if (restoreFocus) trigger.focus();
}

function openMenu(trigger) {
  const menu = trigger.nextElementSibling;
  if (!menu?.classList.contains("dropdown-menu")) return;
  active = { trigger, menu };
  trigger.setAttribute("aria-expanded", "true");
  menu.classList.add("show");
  positionMenu();
  resize.observe(menu);
}

function menuItems(menu) {
  return Array.from(
    menu.querySelectorAll("a[href], button:not(:disabled)"),
  ).filter(
    (item) => item.getClientRects().length && !item.closest(".disabled"),
  );
}

export default function initializeDropdowns() {
  document.querySelectorAll(selector).forEach((trigger) => {
    trigger.setAttribute("aria-expanded", "false");
    if (trigger.tagName === "A") trigger.setAttribute("role", "button");
  });

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest(selector);
    if (trigger) {
      event.preventDefault();
      const wasOpen = active?.trigger === trigger;
      closeMenu();
      if (!wasOpen && !trigger.matches(".disabled, :disabled")) {
        openMenu(trigger);
      }
      return;
    }
    if (!active) return;
    const inMenu = active.menu.contains(event.target);
    if (!inMenu || event.target.closest("a[href], button")) closeMenu(inMenu);
  });

  document.addEventListener("focusin", (event) => {
    if (
      active &&
      !active.trigger.contains(event.target) &&
      !active.menu.contains(event.target)
    ) {
      closeMenu();
    }
  });

  document.addEventListener("keydown", (event) => {
    const trigger = event.target.closest(selector);
    if (trigger && event.key === " " && trigger.tagName === "A") {
      event.preventDefault();
      trigger.click();
    } else if (active && event.key === "Escape") {
      event.preventDefault();
      closeMenu(true);
    } else if (["ArrowDown", "ArrowUp"].includes(event.key)) {
      const inMenu = active?.menu.contains(event.target);
      if (
        !trigger &&
        (!inMenu || event.target.matches("input, select, textarea"))
      ) {
        return;
      }
      event.preventDefault();
      if (trigger && active?.trigger !== trigger) trigger.click();
      if (!active) return;
      const items = menuItems(active.menu);
      const current = items.indexOf(event.target);
      if (!items.length) return;
      const forwards = event.key === "ArrowDown";
      if (current === -1) {
        const first = forwards ? items[0] : items[items.length - 1];
        first.focus();
        return;
      }
      const step = forwards ? 1 : -1;
      const next = (current + step + items.length) % items.length;
      items[next]?.focus();
    }
  });

  window.addEventListener("resize", positionMenu);
  document.addEventListener(
    "scroll",
    (event) => {
      if (active && !active.menu.contains(event.target)) positionMenu();
    },
    true,
  );
}
