import '../css/app.scss'
import './pairing.js'

import $ from 'jquery'

import 'popper.js'
import 'bootstrap'

import checkinInit from "./batchCheckin.js";
import filtersInit from "./filtersInit.js"
import quickSearchInit from "./quickSearch.js"
import multiselectInit from "./multiselect.js"
import bsCustomFileInput from "bs-custom-file-input"

function initializeConfirms() {
  $("[confirm]").click(function(e) {
    if (!window.confirm($(e.target).attr('confirm'))) {
      e.preventDefault();
    }
  })
}

function initializeRevealButtons() {
  $(".content-reveal").click(function(e) {
    e.preventDefault()
    $(e.target).slideUp(250)
    $("#" + $(e.target).data("to-reveal")).slideDown(250)
  })
}

$(document).ready(function(){

    $('.dataEntryForm').submit(function() {
        // Hacky way to figure out if this is a result entry form
        var pmDebater = $('.dataEntryForm :input').filter('#id_pm_debater').val()
        var mgDebater = $('.dataEntryForm :input').filter('#id_mg_debater').val()
        var loDebater = $('.dataEntryForm :input').filter('#id_lo_debater').val()
        var moDebater = $('.dataEntryForm :input').filter('#id_mo_debater').val()
        if(pmDebater && mgDebater && loDebater && moDebater) {
            if(pmDebater == mgDebater || loDebater == moDebater) {
                var response = confirm("You appear to have an iron man, is this correct?")
                if(!response) {
                    return false
                }
            }
        }
        return true;
    });

    $('.tab_card_item').each(function(index, value) {
        $.ajax({
            url:"/team/card/" + $(value).data('team-id') + '/',
            success: function(result) {
                $(value).html(result);
            }
        });
    });

    $('#id_debaters').closest('tr').append(
    "<td><a href=\"/admin/tab/debater/add/\" class=\"add-another btn\" id=\"add_id_debaters\" onclick=\"return showAddAnotherPopup(this);\"> Or Add a Debater Directly</a></td>"
    )

    checkinInit();
    filtersInit();
    quickSearchInit();
    multiselectInit();
    bsCustomFileInput.init();
    initializeConfirms();
    initializeRevealButtons();
});
