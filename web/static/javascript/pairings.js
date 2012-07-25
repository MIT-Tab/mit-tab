$(document).ready(function(){
//$("#sortable").sortable({items: 'td.sortable'});
    saved = null;
    saved_id = null;
    options = {
        revert: true,
        stack: "div",
        opacity: 0.8,
        start: function() {
            saved = $(this).css('backgroundColor');
            saved_id = $(this).attr('judge-id')
            $(this).css('backgroundColor', '#AAAAAA');
        },
        stop: function() {
            $(this).css('backgroundColor', saved);
        }
    };

    drop_options = {
        hoverClass: "ui-state-hover",
        drop: function() {
            alert("Tried to swap "+saved_id+" "+$(this).attr('judge-id'));
        }
    }
 
    $('.judge.sortable').draggable(options)
    $('.judge.sortable').droppable(drop_options)
});
