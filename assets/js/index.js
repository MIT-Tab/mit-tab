import '../css/app.scss'
import './pairing.js'

import $ from 'jquery'

import 'popper.js'
import 'bootstrap'

import quickSearchInit from './quickSearch.js'
import multiselectInit from './multiselect.js'

$(document).ready(function(){
    function filter_on_flags(flags) {
        $('li.filterable').each(function(index, element) {
            var show = 1;
            for (var flag_group in flags) {
                show &= (($(element).data("filters") & flags[flag_group]) > 0);
            }
            show ? $(element).show() : $(element).hide()
        });
    };

    function show_all(type) {
        $(type).show();
    };

    function checkInOrOut(target, isCheckIn) {
        var $target = $(target);
        $target.prop('disabled', true)

        var judgeId = $target.data("judge-id");
        var roundNumber = $target.data("round-number");

        var $label = $("label[for=" + $target.attr("id") + "]")
        $label.text(isCheckIn ? 'Checked In' : 'Checked Out')

        var url = "/judge/" + judgeId + "/check_ins/round/" + roundNumber + "/";
        var requestMethod = isCheckIn ? "POST" : "DELETE";

        $.ajax({
            url: url,
            beforeSend: function(xhr) {
              xhr.setRequestHeader("X-CSRFToken", $("[name=csrfmiddlewaretoken]").val())
            },
            method: requestMethod,
            success: function(resp) {
              $target.prop('disabled', false)
            },
            error: function(_e) {
              $target.prop('disabled', false)
              $target.prop('checked', !isCheckIn)
              $label.text(isCheckIn ? 'Checked Out' : 'Checked In')
              alert('An error occured during check in/out. Refresh the page and try again');
            }
        })
    }

    $('.checkin-toggle').click(function(e) {
        checkInOrOut(e.target, $(e.target).prop('checked'));
    })

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
    
    var dir = 1;
    var olddir = dir
    var timer;
    var pause = 5000;
    var pause_left = 5000;
    var increment = 50;
    
    $(".pairings_toggle").click(function() {
        if($(this).data("toggle")) {
            // Turn the timer off
            $(this).removeClass("active");
            window.clearInterval(timer);
            $(this).data("toggle", 0);
        } else {
            $(this).addClass("active");
            setupScroll();
            $(this).data("toggle", 1);      
        }
    });
    
    function setupScroll() {
        window.clearInterval(timer);
        timer = window.setInterval(scrollWindow, increment);
    }
    
    function scrollWindow() {
        var change_direction = 0;
        if($(window).scrollTop() == $(document).height() - $(window).height()) {
            dir = -1;
            change_direction = 1;
        }
        else if($(window).scrollTop() == 0) {
            dir = 1;
            change_direction = 1;
        }
        var pos = $(window).scrollTop() + 1*dir;
        $(window).scrollTop(pos);
        if (change_direction) {
            window.clearInterval(timer);
            timer = window.setInterval(setupScroll, pause);
        }
    }
    
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

    $(".confirmLink").click(function(e) {
        e.preventDefault();
        confirm("TODO")
        var targetUrl = $(this).attr("href");
    })

    function apply_filters() {
        show_all("li.data-list");
        var flags = 0;
        var filter_groups = {}
        $(".filter").each(function(index, value) {
            if (value.checked) {
                filter_groups[$(value).data("filter-group")] |= $(value).data("filter");
                flags |= $(value).data("filter");
            }
        });
        filter_on_flags(filter_groups);
    }

    console.log($(".filter"))
    $(".filter").change(console.log.bind("change"))
    $(".filter").change(apply_filters)

    apply_filters();
    quickSearchInit();
    multiselectInit();
});
