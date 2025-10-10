import "../css/pairing-display.scss";
import "./scroller";
import "./displayMemberNames";

function adjustFooterSpacer() {
  const footer = document.querySelector(".pairings_footer");
  const spacer = document.querySelector(".pairings_footer_spacer");
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
  setTimeout(adjustFooterSpacer, 0)
);
