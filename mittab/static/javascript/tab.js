jQuery.expr[':'].Contains = function(a,i,m){
    return jQuery(a).text().toUpperCase().indexOf(m[3].toUpperCase())>=0;
};

$(document).ready(function(){
    function filter(matching_text) {
        $('li.searchable:not(:Contains(' + matching_text+ '))').hide(); 
        $('li.searchable:Contains(' + matching_text + ')').show();
    };

    filter_on_flags = function (flags) {
        $('li.filterable').each(function(index, element) {
            var show = 1;
            for (var flag_group in flags) {
                show &= (($(element).data("filters") & flags[flag_group]) > 0);
            }
            if (!show) {
                $(element).hide();
            } else {
                $(element).show();
            }
        });
    };

    function show_all(type) {
        $(type).show();
    };

    $('#quick-search').keyup(function() {
        if ($(this).val()) {
            filter($(this).val());
        }
        else {
            show_all("li");
        }
    });

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
    var timer, pb_timer;
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
        window.clearInterval(pb_timer);
        $('#progressbar').data('count', -1);
        progress();
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
            $('#progressbar').width(100);
            $('#progressbar').height(30);
            pb_timer = window.setInterval(progress, increment);
        }
    }
    
    function progress() {
        var pb = $('#progressbar')
        pb.data('count', pb.data('count') + 1);
        pb.progressbar("value", 100*(increment * pb.data('count')) / pause);
    }

    $('#progressbar').progressbar({
        value: 0
    });
    $('.tab_card_item').each(function(index, value) {
        $.ajax({
            url:"/team/card/" + $(value).data('team-id') + '/',
            success: function(result) {
                var pb = $('#progressbar')
                $(value).html(result);
                pb.data('count', pb.data('count') + 1);
                pb.progressbar("value", 100*(pb.data('count') / pb.data('max')));
                if(pb.progressbar("value") == 100) {
                    pb.remove()
                }
            }
        });
    });

    $('#id_debaters').closest('tr').append(
    "<td><a href=\"/admin/tab/debater/add/\" class=\"add-another btn\" id=\"add_id_debaters\" onclick=\"return showAddAnotherPopup(this);\"> Or Add a Debater Directly</a></td>"
    )

    //  Taken from stackoverflow
    $("#dialog").dialog({
        autoOpen: false,
        modal: true
    });
 
    $(".confirmLink").click(function(e) {
        e.preventDefault();
        var targetUrl = $(this).attr("href");

        $("#dialog").dialog({
        buttons : {
            "Confirm" : function() {
            window.location.href = targetUrl;
            },
            "Cancel" : function() {
            $(this).dialog("close");
            }
        }
        });
        $("#dialog").dialog("open");
    });
    //  End taken from stackoverflow

    apply_filters = function() {
        show_all("li.data_list");
        var flags = 0;
        var filter_groups = {}
        $(".filter").each(function(index, value) {
            console.log(flags)
            if (value.checked) {
                filter_groups[$(value).data("filter-group")] |= $(value).data("filter");
                flags |= $(value).data("filter");
            }
        });
        filter_on_flags(filter_groups);
    }

    $(".filter").change(function() {
        apply_filters();
    });

    apply_filters();
    $("[multiple]").multiselect({
      enableFiltering: true,
      buttonClass: 'btn btn-outline-secondary',
      buttonWidth: "20em",
      enableCaseInsensitiveFiltering: true
    })
});
