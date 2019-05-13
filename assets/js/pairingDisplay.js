import '../css/pairing-display.scss'
import $ from 'jquery'

$(document).ready(function() {
  var timer
  var increment = 50
  var pause = 5000
  var dir = 1
  var olddir = dir
  var pause_left = 5000


  $("#autoscroll").change(function() {
    if($(this).prop("checked")) {
      setupScroll()
    } else {
      window.clearInterval(timer)
    }
  })

  function setupScroll() {
    window.clearInterval(timer)
    timer = window.setInterval(scrollWindow, increment)
  }

  function scrollWindow() {
    var change_direction = 0
    if($(window).scrollTop() == $(document).height() - $(window).height()) {
      dir = -1
      change_direction = 1
    }
    else if($(window).scrollTop() == 0) {
      dir = 1
      change_direction = 1
    }
    var pos = $(window).scrollTop() + 1 * dir
    $(window).scrollTop(pos)
    if (change_direction) {
      window.clearInterval(timer)
      timer = window.setInterval(setupScroll, pause)
    }
  }

  setupScroll()
})
