import "../css/app.scss";
import "./pairing";

import $ from "jquery";

import "popper.js";
import "bootstrap";

import bsCustomFileInput from "bs-custom-file-input";
import ballotsInit from "./ballots";
import checkinInit from "./batchCheckin";
import roomCheckinInit from "./roomBatchCheckin";
import filtersInit from "./filters";
import quickSearchInit from "./quickSearch";
import multiselectInit from "./multiselect";

function initializeConfirms() {
  $("[confirm]").click(e => {
    if (!window.confirm($(e.target).attr("confirm"))) {
      e.preventDefault();
    }
  });
}

function initializeRevealButtons() {
  $(".content-reveal").click(e => {
    e.preventDefault();
    $(e.target).slideUp(250);
    $(`#${$(e.target).data("to-reveal")}`).slideDown(250);
  });
}

function loadTabCards() {
  $(".tab_card_item").each((index, value) => {
    $.ajax({
      url: `/team/card/${$(value).data("team-id")}`,
      success(result) {
        $(value).html(result);
      }
    });
  });
}

$(document).ready(() => {
  ballotsInit();
  checkinInit();
  roomCheckinInit();
  filtersInit();
  quickSearchInit();
  multiselectInit();
  bsCustomFileInput.init();

  initializeConfirms();
  initializeRevealButtons();
  loadTabCards();
});
