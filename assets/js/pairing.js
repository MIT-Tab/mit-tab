import quickSearchInit from './quickSearch.js'

var populate_tab_card = function(tab_card_element) {
    var team_id = tab_card_element.attr('team-id')
    $.ajax({
        url:"/team/" + team_id + "/stats/",
        success: function(result) {
            result = result.result
            var text = [result.wins, result.total_speaks.toFixed(2), result.govs, result.opps, result.seed].join(" / ")
            tab_card_element.html(
              "<abbr title='Wins / Speaks / Govs / Opps / Seed'>" + text + "</abbr>"
            )
        },
    })
}

var populate_alternative_teams = function() {
    var $parent = $(this).parent()
    var teamId = $parent.attr('team-id');
    var roundId = $parent.attr('round-id');
    var position = $parent.attr('position')
    var url = "/round/" + roundId + "/"+ teamId + "/alternative_teams/" + position;

    $.ajax({
        url: url,
        success: function(result) {
            $parent.find(".dropdown-menu").html(result);
            $parent.find(".dropdown-menu").find(".team-swap").click(assignTeam);
            quickSearchInit($parent.find("#quick-search"))
        },
    })
}

var populate_alternative_judges = function() {
    var $parent = $(this).parent()
    var judge_id = $parent.attr('judge-id');
    var round_id = $parent.attr('round-id');
    var populate_url = "/round/" + round_id + "/alternative_judges/";

    if (judge_id) {
        populate_url +=  judge_id + "/";
    }

    $.ajax({
        url: populate_url,
        success: function(result) {
            $parent.find(".dropdown-menu").html(result);
            $parent.find(".dropdown-menu").find(".judge-assign").click(assign_judge);
            quickSearchInit($parent.find("#quick-search"))
        },
    })
}

var assign_judge = function(e) {
    e.preventDefault()
    var round_id = $(e.target).attr('round-id');
    var judge_id = $(e.target).attr('judge-id');
    var current_judge_id = $(e.target).attr('current-judge-id');
    var assign_url = "/round/" + round_id + "/assign_judge/" + judge_id + "/";
    if (current_judge_id) {
        assign_url += current_judge_id + "/";
    }

    var $buttonWrapper;
    if (current_judge_id) {
        $buttonWrapper = $("span[round-id="+round_id+"][judge-id="+current_judge_id+"]");
    } else {
        $buttonWrapper = $("span[round-id="+round_id+"].unassigned").first()
    }
    var $button = $buttonWrapper.find('.btn-sm')
    $button.addClass('disabled')

    var judge_button;
    $.ajax({
        url: assign_url,
        success: function(result) {
            $button.removeClass('disabled')
            $buttonWrapper.removeClass('unassigned')
            $buttonWrapper.attr('judge-id', result.judge_id);
            $button.html(result.judge_name + " <small>(" + result.judge_rank.toFixed(2) + ")")
            $(".judges span[round-id=" + round_id + "] .judge-toggle").removeClass("chair")
            $(".judges span[round-id=" + round_id + "][judge-id=" + result.chair_id + "] .judge-toggle").addClass("chair")
        },
    });
}

function assignTeam(e) {
    var teamId = $(e.target).attr('team-id')
    var oldTeamId = $(e.target).attr('src-team-id')
    var roundId = $(e.target).attr('round-id')
    var position = $(e.target).attr('position')
    var url = "/pairings/assign_team/" + roundId + "/" + position + "/" + teamId
    var alertMsg = 'An error occured. Refresh the page and try to fix any inconsistencies you may notice.'

    $.ajax({
        url: url,
        success: function(result) {
            if (result.success) {
                var $container = $(".row[round-id=" + roundId + "] ." + position + '-team')
                $container.find('.team-swap').attr('team-id', result.team.id)
                $container.find('.team-link').text(result.team.name)
                $container.find('.team-link').attr('href', '/team/' + result.team.id)
                $container.find('.tabcard').attr('team-id', result.team.id)

                populate_tab_card($('.tabcard[team-id=' + result.team.id + ']'))

                $oldTeamTabcard = $('.tabcard[team-id=' + oldTeamId + ']')
                $oldTeamTabcard = populate_tab_card($oldTeamTabcard)
            } else {
                alert(alertMsg)
            }
        },
        failure: function() {
            alert(alertMsg)
        }
    })
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

var toggle_pairing_release = function(event) {
    event.preventDefault();
    var button = $(this);
    $.ajax({
        url:"/pairing/release",
        success: function(result) {
            if (result.pairing_released) {
                $("#close-pairings").removeClass("d-none")
                $("#release-pairings").addClass("d-none")
            } else {
                $("#close-pairings").addClass("d-none")
                $("#release-pairings").removeClass("d-none")
            }
        },
    });
}

var bind_handlers = function() {
    $('.team.tabcard').each(function(index, element) {
        populate_tab_card($(element))
    })
    $('#team_ranking').each(function(index, element) {
        lazy_load($(element).parent(), "/team/rank/");
    })
    $('#debater_ranking').each(function(index, element) {
        lazy_load($(element).parent(), "/debater/rank/");
    })

    $('.judge-toggle').click(populate_alternative_judges);
    $('.team-toggle').click(populate_alternative_teams);
    $('.alert-link').click(alert_link);
    $('select[name=winner]').change(select_info);
    $('.btn.release').click(toggle_pairing_release);
}

$(document).ready(bind_handlers)
