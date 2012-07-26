$(document).ready(function(){
//$("#swappable").sortable({items: 'td.sortable'});
    saved = null;
    saved_id = null;
    saved_round = null;
    judge_drag_options = {
        revert: true,
        stack: "div",
        opacity: 0.8,
        start: function() {
            saved = $(this).css('backgroundColor')
            $(this).css('backgroundColor', '#AAAAAA')
        },
        stop: function() {
            $(this).css('backgroundColor', saved);
        }
    };

    judge_drop_options = {
        accept: ".judge.swappable",
        hoverClass: "ui-state-hover",
        drop: function(event, ui) {
            from_round = ui.draggable.attr('round-id')
            from_judge = ui.draggable.attr('judge-id')
            dest_round = $(this).attr('round-id')
            dest_judge = $(this).attr('judge-id')
            dest_obj = $(this)
            $.ajax({url:"/pairings/swap/" +
                        from_round + "/" + from_judge +
                        "/with/" + dest_round + "/" + dest_judge + "/",
                success: function(result) {
                    if(result.success) {
                        new_text = dest_obj.text()
                        dest_obj.text(ui.draggable.text())
                        ui.draggable.text(new_text)
                    }
                }
            });
        }
    }
 
    $('.judge.swappable').draggable(judge_drag_options)
    $('.judge.swappable').droppable(judge_drop_options)

    team_drag_options = {
        revert: true,
        stack: "div",
        opacity: 0.9,
        start: function() {
            saved = $(this).css('backgroundColor')
            saved_id = $(this).attr('team-id')
            $(this).css('backgroundColor', '#AAAAAA')
        },
        stop: function() {
            $(this).css('backgroundColor', saved)
        }
    };

    team_drop_options = {
        hoverClass: "ui-state-hover",
        accept: ".team.swappable",
        drop: function() {
            alert("Tried to swap "+saved_id+" "+$(this).attr('team-id'));
        }
    }

    $('.team.swappable').draggable(team_drag_options)
    $('.team.swappable').droppable(team_drop_options)

});
