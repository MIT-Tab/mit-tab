from django.test import TestCase
import mittab.tab_logic

class TabLogicTestCase(TestCase):
    """ Currently doesn't work """
    fixtures = ['test_db.json']

    def setUp(self):
        pass

    def test_foo(self):
        print "Ran test"

