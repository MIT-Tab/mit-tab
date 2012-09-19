jQuery.expr[':'].Contains = function(a,i,m){
    return jQuery(a).text().toUpperCase().indexOf(m[3].toUpperCase())>=0;
};

$(document).ready(function(){
    
    function filter(matching_text) {
        $('li.data_list:not(:Contains(' + matching_text+ '))').hide(); 
        $('li.data_list:Contains(' + matching_text + ')').show();
    };
    
    function show_all(type) {
        $(type).show();
    }
    
    $('#filter_box').keyup(function() {
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
    var timer; 
    if($('#scrollPage').length != 0) {
        $(window).scrollTop(0);
        timer = window.setInterval(setupScroll, 5000);
    }
    
    $('.pairings_table').mouseenter(function() {
        olddir = dir
        dir = 0;        
    });
    $('.pairings_table').mouseleave(function() {
        dir = olddir;        
    });
 
    function setupScroll() {
        window.clearInterval(timer);
        timer = window.setInterval(scrollWindow, 100);
    }
    
    function scrollWindow() { 
        if($(window).scrollTop() == $(document).height() - $(window).height()) {
            dir = -1;
        }
        else if($(window).scrollTop() == 0) {
            dir = 1;
        }
        var pos = $(window).scrollTop() + 1*dir;
        $(window).scrollTop(pos);
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
});
