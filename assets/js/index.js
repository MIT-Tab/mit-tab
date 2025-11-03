import "../css/app.scss";
import "../css/mobile.scss"
import "../css/navigation.scss"

import "./pairing";
import "./outround";

import $ from "jquery";

import "popper.js";
import "bootstrap";

import bsCustomFileInput from "bs-custom-file-input";
import ballotsInit from "./ballots";
import filtersInit from "./filters";
import quickSearchInit from "./quickSearch";
import multiselectInit from "./multiselect";

function initializeConfirms() {
  $("[confirm]").click((e) => {
    if (!window.confirm($(e.target).attr("confirm"))) {
      e.preventDefault();
    }
  });
}

function initializeRevealButtons() {
  $(".content-reveal").click((e) => {
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
      },
    });
  });
}

function initializeTooltips() {
  $('[data-toggle="tooltip"]').tooltip();
}

function initializeSettingsForm() {
  $(".custom-control-input").on(
    "change",
    function handleCustomControlInputChange() {
      const label = $(this).siblings(".custom-control-label");
      if ($(this).is(":checked")) {
        label.text("Enabled");
      } else {
        label.text("Disabled");
      }
    },
  );
}

$(document).ready(() => {
  ballotsInit();
  filtersInit();
  quickSearchInit();
  multiselectInit();
  bsCustomFileInput.init();
  initializeTooltips();
  initializeSettingsForm();

  initializeConfirms();
  initializeRevealButtons();
  loadTabCards();
});
