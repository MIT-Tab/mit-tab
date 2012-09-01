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


$(document).ready(function(){
    judge_drag_options = {
        revert: true,
        stack: "div",
        opacity: 0.8,
        start: function() {
            // Insert logic to color bad things
        },
        stop: function() {
        }
    };

    judge_drop_options = {
        accept: ".judge.swappable",
        hoverClass: "ui-state-hover",
        drop: function(event, ui) {
            var from_round_id = ui.draggable.attr('round-id')
            var from_judge_id = ui.draggable.attr('judge-id')
            var from_judge_name = ui.draggable.attr('judge-name')
            var dest_round_id = $(this).attr('round-id')
            var dest_judge_id = $(this).attr('judge-id')
            var dest_judge_name = $(this).attr('judge-name')
            var dest_obj = $(this)
            $.ajax({
                url:"/pairings/swap/" +
                    from_round_id + "/" + from_judge_id +
                    "/with/" + dest_round_id + "/" + dest_judge_id + "/",
                success: function(result) {
                    if(result.success) {
                        new_from_html = 
                            "<a href=\"/judge/" + dest_judge_id + "/\">" + 
                            dest_judge_name + "</a>"
                        new_dest_html = 
                            "<a href=\"/judge/" + from_judge_id + "/\">" + 
                            from_judge_name + "</a>"
                        
                        ui.draggable.html(new_from_html)
                        ui.draggable.attr('round-id', from_round_id)
                        ui.draggable.attr('judge-id', dest_judge_id)
                        ui.draggable.attr('judge-name', dest_judge_name)
                        
                        dest_obj.html(new_dest_html)
                        dest_obj.attr('round-id', dest_round_id)
                        dest_obj.attr('judge-id', from_judge_id)
                        dest_obj.attr('judge-name', from_judge_name)
                    }
                    else {
                        alert("unable to swap those two judges, use the admin interface");
                    }
                },
                error: function(result) {
                    alert("unable to swap those two judges, use the admin interface");
                }
            });
        }
    }
 
    team_drag_options = {
        revert: true,
        stack: "div",
        opacity: 0.9,
        start: function() {
            // insert bad drag drop logic
        },
        stop: function() {
        }
    };

    team_drop_options = {
        hoverClass: "ui-state-hover",
        accept: ".team.swappable",
        drop: function(event, ui) {
            var from_round_id = ui.draggable.attr('round-id')
            var from_team_id = ui.draggable.attr('team-id')
            var from_team_name = ui.draggable.attr('team-name')
            var from_team_tabcard = $($('.team.tabcard[team-id='+from_team_id+']'))
            var dest_round_id = $(this).attr('round-id')
            var dest_team_id = $(this).attr('team-id')
            var dest_team_name = $(this).attr('team-name')
            var dest_obj = $(this)
            var dest_team_tabcard= $($('.team.tabcard[team-id='+dest_team_id+']'))
            $.ajax({
                url:"/pairings/swap_team/" +
                    from_round_id + "/" + from_team_id +
                    "/with/" + dest_round_id + "/" + dest_team_id + "/",
                success: function(result) {
                    if(result.success) {
                        new_from_html = 
                            "<a href=\"/team/" + dest_team_id + "/\">" + 
                            dest_team_name + "</a>"
                        new_dest_html = 
                            "<a href=\"/team/" + from_team_id + "/\">" + 
                            from_team_name + "</a>"
                        
                        ui.draggable.html(new_from_html)
                        ui.draggable.attr('round-id', from_round_id)
                        ui.draggable.attr('team-id', dest_team_id)
                        ui.draggable.attr('team-name', dest_team_name)
                        from_team_tabcard.attr('team-id', dest_team_id)

                        dest_obj.html(new_dest_html)
                        dest_obj.attr('round-id', dest_round_id)
                        dest_obj.attr('team-id', from_team_id)
                        dest_obj.attr('team-name', from_team_name)
                        dest_team_tabcard.attr('team-id', from_team_id)
                        populate_tab_card(from_team_tabcard)
                        populate_tab_card(dest_team_tabcard)
                    }
                    else {
                        alert("unable to swap those two teams, use the admin interface");
                    }
                },
                error: function(result) {
                    alert("unable to swap those two teams, use the admin interface");
                }
            });
        }
    }
    bind_handlers()
});

var populate_tab_card = function(tab_card_element) {
    var team_id = tab_card_element.attr('team-id')
    $.ajax({
        url:"/team/" + team_id + "/stats/",
        success: function(result) {
            result = result.result
            var text = [result.wins, result.total_speaks.toFixed(2), result.govs, result.opps].join(" / ")
            tab_card_element.html("<a href=\"/team/card/"+team_id+"\">"+text+"</a>")
        },
    })
}

var bind_handlers = function() {
    $('.judge.swappable').draggable(judge_drag_options)
    $('.judge.swappable').droppable(judge_drop_options)

    $('.team.swappable').draggable(team_drag_options)
    $('.team.swappable').droppable(team_drop_options)

    $('.team.tabcard').each(function(index, element) {
        populate_tab_card($(element))
    })

}



