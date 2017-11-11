var swap_element = function(from, dest, element_type) {
    var from_round_id = from.attr('round-id')
    var from_judge_id = from.attr('judge-id')
    var from_judge_name = from.attr('judge-name')
    var dest_round_id = dest.attr('round-id')
    var dest_judge_id = dest.attr('judge-id')
    var dest_judge_name = dest.attr('judge-name')
    new_from_html =
        "<a href=\"/judge/" + dest_judge_id + "/\">" +
        dest_judge_name + "</a>"
    new_dest_html =
        "<a href=\"/judge/" + from_judge_id + "/\">" +
        from_judge_name + "</a>"

    from.html(new_from_html)
    from.attr('round-id', from_round_id)
    from.attr('judge-id', dest_judge_id)
    from.attr('judge-name', dest_judge_name)

    dest.html(new_dest_html)
    dest.attr('round-id', dest_round_id)
    dest.attr('judge-id', from_judge_id)
    dest.attr('judge-name', from_judge_name)
}


var populate_tab_card = function(tab_card_element) {
    var team_id = tab_card_element.attr('team-id')
    $.ajax({
        url:"/team/" + team_id + "/stats/",
        success: function(result) {
            result = result.result
            var text = [result.wins, result.total_speaks.toFixed(2), result.govs, result.opps, result.seed].join(" / ")
            tab_card_element.html("<a class=\"btn btn-link\" href=\"/team/card/"+team_id+"\">"+text+"</a>")
        },
    })
}


var populate_alternative_judges = function() {
    var round_id = $(this).attr('round-id');
    var judge_id = $(this).attr('judge-id');
    var judge_position = $(this).attr('judge-pos');
    var populate_url = "/round/" + round_id + "/alternative_judges/";
    var judge_list;
    if (judge_id) {
        populate_url +=  judge_id + "/";
    }
    $.ajax({
        url: populate_url,
        success: function(result) {
            if (judge_id) {
                judge_list = $("ul[round-id="+round_id+"][judge-id="+judge_id+"]");
            } else {
                judge_list = $("ul[round-id="+round_id+"][judge-pos="+judge_position+"]");
            }
            $(judge_list).html(result);
            bind_handlers();
        },
    })
}

var assign_judge = function() {
    var round_id = $(this).attr('round-id');
    var judge_id = $(this).attr('judge-id');
    var current_judge_id = $(this).attr('current-judge-id');
    var assign_url = "/round/" + round_id + "/assign_judge/" + judge_id + "/";
    if (current_judge_id) {
        assign_url += current_judge_id + "/";
    }

    var judge_button;
    $.ajax({
        url: assign_url,
        success: function(result) {
            if (current_judge_id) {
                judge_button = $("span[round-id="+result.round_id+"][judge-id="+current_judge_id+"]");
            } else {
                judge_button = $("span[round-id="+result.round_id+"].unassigned").first()
            }
            var html = "<a class=\"btn btn-link dropdown-toggle\" data-toggle=\"dropdown\"" +
                       "round-id=" + result.round_id + " judge-id=" + result.judge_id + " href=\"#\">"+ result.judge_name + " (" + result.judge_rank +")" +
                       "  <span class=\"caret\"></span></a><ul class=\"dropdown-menu\" round-id="+
                        result.round_id+" judge-id=" + result.judge_id + "></ul>";
            $(judge_button).html(html);
            $(judge_button).attr('judge-id', result.judge_id);
            $(judge_button).removeClass('unassigned');
            bind_handlers();
        },
    });
}


var lazy_load = function(element, url) {
    element.addClass("loading");
    $.ajax({
        url:url,
        success: function(result) {
            element.html(result);
            element.removeClass("loading");
        },
        failure: function(result) {
            element.html("Error received from server");
            element.removeClass("loading");
        }
    })
}

var alert_link = function(e) {
    alert("Note that you have assigned a judge from within the pairing. You need to go and fix that round now.");
}

var select_info = function(element) {
    var div = $("div[data-option="+$(this).val()+"]");
    $(".winner").each(function(i,e){$(e).addClass("hidden")});
    $(div).removeClass("hidden")
}

var toggle_pairing_release = function(element) {
    var button = $(this);
    $.ajax({
        url:"/pairing/release",
        success: function(result) {
            if (result.pairing_released) {
                button.text("Close Pairings");
                button.removeClass("btn-warning");
                button.addClass("btn-success");
            } else {
                button.text("Release Pairings");
                button.removeClass("btn-success");
                button.addClass("btn-warning");
            }
        },
    });
}

var bind_handlers = function() {
    $('.team.tabcard').each(function(index, element) {
        populate_tab_card($(element))
    })
    $('#team_ranking').each(function(index, element) {
        lazy_load($(element), "/team/rank/");
    })
    $('#debater_ranking').each(function(index, element) {
        lazy_load($(element), "/debater/rank/");
    })

    $('.dropdown-toggle').click(populate_alternative_judges);
    $('.judge-assign').click(assign_judge);
    $('.alert-link').click(alert_link);
    $('select[name=winner]').change(select_info);
    $('.btn.release').click(toggle_pairing_release);
}
