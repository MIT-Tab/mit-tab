import $ from 'jquery'
import select2 from 'select2'
import 'select2/dist/css/select2.css'
import '@ttskch/select2-bootstrap4-theme/dist/select2-bootstrap4.min.css'

function multiselectInit($elem) {
  if (!$elem) {
    $elem = $('#data-entry-form select[multiple]')
  }
  $elem.select2({ theme: 'bootstrap4' })
}

export default multiselectInit
