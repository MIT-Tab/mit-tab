import "../css/app.scss";
import "../css/mobile.scss";
import "../css/navigation.scss";
import "../css/tab-cards.scss";
import "../css/judge-speaker-points.scss";

import "./ui";
import "./pairing";
import "./outround";

import $ from "jquery";

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

function initializeSettingsForm() {
  $("[data-toggle-label]")
    .on("change", function handleCustomControlInputChange() {
      const label = $(this).siblings(".custom-control-label");
      const onText = $(this).data("label-on") || "Enabled";
      const offText = $(this).data("label-off") || "Disabled";
      label.text($(this).is(":checked") ? onText : offText);
    })
    .trigger("change");
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
  initializeSettingsForm();
  initializeThemeColorPicker();

  initializeConfirms();
  initializeRevealButtons();
});
