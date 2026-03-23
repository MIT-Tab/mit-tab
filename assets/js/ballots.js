import $ from "jquery";

function selectInfo() {
  const div = $(`div[data-option=${$(this).val()}]`);
  $(".winner").each((_, el) => $(el).addClass("hidden"));
  $(div).removeClass("hidden");
}

function checkSpeaksWarnings(form) {
  // Get settings from window object (set in template)
  const settings = window.speakSettings || {
    warnJudgesAboutSpeaks: true,
    lowSpeakWarningThreshold: 25,
    highSpeakWarningThreshold: 34,
  };

  // If warnings are disabled, proceed
  if (!settings.warnJudgesAboutSpeaks) {
    return true;
  }

  // Check the four hardcoded speaker fields
  const pmSpeaks = parseFloat($(form).find("#id_pm_speaks").val());
  const mgSpeaks = parseFloat($(form).find("#id_mg_speaks").val());
  const loSpeaks = parseFloat($(form).find("#id_lo_speaks").val());
  const moSpeaks = parseFloat($(form).find("#id_mo_speaks").val());

  const speaks = [pmSpeaks, mgSpeaks, loSpeaks, moSpeaks];

  const highSpeaks = speaks.filter(
    (s) => s >= settings.highSpeakWarningThreshold,
  );
  const lowSpeaks = speaks.filter(
    (s) => s <= settings.lowSpeakWarningThreshold,
  );

  if (highSpeaks.length > 0 || lowSpeaks.length > 0) {
    let message = "";
    if (highSpeaks.length > 0) {
      message += `Please note: you have entered some very high speaks (${highSpeaks.join(
        ", ",
      )}).\n\n`;
    }
    if (lowSpeaks.length > 0) {
      message += `Please note: you have entered some very low speaks (${lowSpeaks.join(
        ", ",
      )}).\n\n`;
    }
    message +=
      "If you are confident in these scores, you may proceed with submission. Otherwise, please review the speaker scale guide.\n\nProceed with ballot?";

    return window.confirm(message);
  }

  return true;
}

function ballotsInit() {
  $("select[name=winner]").change(selectInfo);

  $(".ballot-form.single").submit((e) => {
    const pmDebater = $(e.target).find("#id_pm_debater").val();
    const mgDebater = $(e.target).find("#id_mg_debater").val();
    const loDebater = $(e.target).find("#id_lo_debater").val();
    const moDebater = $(e.target).find("#id_mo_debater").val();

    if (pmDebater && mgDebater && loDebater && moDebater) {
      if (pmDebater === mgDebater || loDebater === moDebater) {
        if (
          !window.confirm("You appear to have an iron man, is this correct?")
        ) {
          return false;
        }
      }
    }

    // Check speaks warnings
    return checkSpeaksWarnings(e.target);
  });

  // Handle multiple ballot form (panels)
  $(".ballot-form:not(.single)").submit((e) => {
    return checkSpeaksWarnings(e.target);
  });
}

export default ballotsInit;
