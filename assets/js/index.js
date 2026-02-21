import "../css/app.scss";
import "../css/mobile.scss";
import "../css/navigation.scss";

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

function initializeThemeColorPicker() {
  const hexPattern = /^#[0-9a-fA-F]{6}$/;

  $(".theme-color-picker").each((_, pickerEl) => {
    const picker = $(pickerEl);
    const targetId = picker.data("target-input");
    const textInput = $(`#${targetId}`);

    if (!textInput.length) {
      return;
    }

    const syncPickerFromText = () => {
      const value = (textInput.val() || "").toString().trim();
      if (hexPattern.test(value)) {
        picker.val(value);
      }
    };

    const syncTextFromPicker = () => {
      textInput.val((picker.val() || "").toString().toUpperCase());
    };

    syncPickerFromText();

    textInput.on("input", syncPickerFromText);
    textInput.on("blur", () => {
      const value = (textInput.val() || "").toString().trim();
      if (hexPattern.test(value)) {
        textInput.val(value.toUpperCase());
      }
    });
    picker.on("input change", syncTextFromPicker);
  });
}

$(document).ready(() => {
  ballotsInit();
  filtersInit();
  quickSearchInit();
  multiselectInit();
  bsCustomFileInput.init();
  initializeTooltips();
  initializeSettingsForm();
  initializeThemeColorPicker();

  initializeConfirms();
  initializeRevealButtons();
  loadTabCards();
});
