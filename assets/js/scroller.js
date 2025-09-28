import $ from "jquery";

const increment = 50;
const pause = 5000;

let timer;
let dir = 1;

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
  $("#autoscroll").change(function handleAutoscrollChange() {
    if ($(this).prop("checked")) {
      setupScroll();
    } else {
      window.clearInterval(timer);
    }
  });
});
