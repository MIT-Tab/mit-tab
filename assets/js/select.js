import $ from 'jquery'
import select2 from 'select2'
import 'select2/dist/css/select2.css'
import '@ttskch/select2-bootstrap4-theme/dist/select2-bootstrap4.min.css'

function selectInit($elem) {
  $elem.select2({ theme: 'bootstrap4' })
}

export default selectInit
