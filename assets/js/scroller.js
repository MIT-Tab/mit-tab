import $ from "jquery";

const increment = 50;
const pause = 500;

let timer;
let dir = 1;

const adjustFloatingOffsets = () => {
  const nav = document.querySelector("nav.navbar");
  if (!nav) {
    return;
  }

  const navHeight = nav.offsetHeight;
  const adjustments = [
    { header: ".pairings_header", spacer: ".pairings_header_spacer" },
    { header: ".public_header", spacer: ".public_header_spacer" }
  ];

  adjustments.forEach(({ header, spacer }) => {
    document.querySelectorAll(header).forEach(headerEl => {
      // eslint-disable-next-line no-param-reassign
      headerEl.style.top = `${navHeight}px`;
      const spacerEl = document.querySelector(spacer);
      if (spacerEl) {
        const computedHeight =
          headerEl.offsetHeight || headerEl.clientHeight || 0;
        spacerEl.style.height = `${computedHeight + 16}px`;
      }
    });
  });
};

let scrollWindow = null;
let setupScroll = null;

scrollWindow = () => {
  let changeDirection = 0;
  if ($(window).scrollTop() === $(document).height() - $(window).height()) {
    dir = -1;
    changeDirection = 1;
  } else if ($(window).scrollTop() === 0) {
    dir = 1;
    changeDirection = 1;
  }
  const pos = $(window).scrollTop() + 1 * dir;
  $(window).scrollTop(pos);
  if (changeDirection) {
    window.clearInterval(timer);
    timer = window.setInterval(setupScroll, pause);
  }
};

setupScroll = () => {
  window.clearInterval(timer);
  timer = window.setInterval(scrollWindow, increment);
};

$(document).ready(() => {
  adjustFloatingOffsets();
  $(window).on("resize", adjustFloatingOffsets);

  $("#autoscroll").change(function handleAutoscrollChange() {
    if ($(this).prop("checked")) {
      setupScroll();
    } else {
      window.clearInterval(timer);
    }
  });
});
