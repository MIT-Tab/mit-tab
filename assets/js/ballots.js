import $ from "jquery";

function selectInfo() {
  const div = $(`div[data-option=${$(this).val()}]`);
  $(".winner").each((_, el) => $(el).addClass("hidden"));
  $(div).removeClass("hidden");
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
        return window.confirm(
          "You appear to have an iron man, is this correct?",
        );
      }
    }
    return true;
  });
}

export default ballotsInit;
