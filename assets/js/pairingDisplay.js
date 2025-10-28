function adjustFooterSpacer() {
  const footer = document.querySelector(".public_footer");
  const spacer = document.querySelector(".public_footer_spacer");
  if (footer && spacer) {
    spacer.style.height = `${footer.offsetHeight}px`;
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", adjustFooterSpacer);
} else {
  adjustFooterSpacer();
}

window.addEventListener("resize", adjustFooterSpacer);
window.addEventListener("memberNamesToggled", () =>
  setTimeout(adjustFooterSpacer, 0),
);
