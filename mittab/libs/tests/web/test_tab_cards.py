"""Original tab card tables fit mobile screens and support browser zoom."""
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import caches
from django.urls import reverse
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from mittab.apps.tab.models import Outround, TabSettings, Team
from mittab.libs.cacheing import cache_logic


class TabCardsWebTestCase(StaticLiveServerTestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        caches["public"].clear()
        cache_logic.clear_cache()
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--hide-scrollbars")
        self.driver = webdriver.Chrome(options=options)
        self.addCleanup(self.driver.quit)
        self.addCleanup(cache_logic.clear_cache)
        self.addCleanup(caches["public"].clear)
        TabSettings.set("publish_tab_cards_with_first_outround", 1)
        TabSettings.set("var_teams_visible", 2)
        Outround.objects.create(
            gov_team=Team.objects.first(), opp_team=Team.objects.last(),
            type_of_round=Outround.VARSITY, num_teams=2,
        )
        opponent = Team.objects.first().gov_team.first().opp_team
        opponent.name = "W" * Team._meta.get_field("name").max_length
        opponent.save(update_fields=["name"])

    def test_phone_tablet_desktop_and_print_layouts(self):
        self.driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True,
        })
        self.driver.get(self.live_server_url + reverse("public_tab_cards"))
        WebDriverWait(self.driver, 15).until(
            lambda driver: driver.find_elements(By.CSS_SELECTOR, ".tab-cards .tab-card")
        )
        for width in (320, 390, 768):
            with self.subTest(width=width):
                self.driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
                    "width": width, "height": 900, "deviceScaleFactor": 1, "mobile": True,
                })
                metrics = self.driver.execute_script("""
                    const table = document.querySelector('.tab-card');
                    const cells = [...table.querySelectorAll('tbody td')];
                    return {
                        viewport: innerWidth,
                        page: document.documentElement.scrollWidth,
                        cells: cells.map(cell => {
                            const rect = cell.getBoundingClientRect();
                            return {left: rect.left, right: rect.right,
                                    display: getComputedStyle(cell.parentElement).display};
                        }),
                        header: getComputedStyle(table.querySelector('thead')).display,
                        printButton: document.querySelector('.tab-cards__intro .btn')
                            .getBoundingClientRect().height,
                    };
                """)
                self.assertEqual(metrics["viewport"], width)
                self.assertLessEqual(metrics["page"], metrics["viewport"])
                self.assertGreaterEqual(metrics["printButton"], 44)
                for cell in metrics["cells"]:
                    self.assertGreaterEqual(cell["left"], 0)
                    self.assertLessEqual(cell["right"], metrics["viewport"])
                    self.assertEqual(cell["display"], "table-row")
                self.assertEqual(metrics["header"], "table-header-group")
                self.driver.execute_cdp_cmd("Emulation.setPageScaleFactor", {
                    "pageScaleFactor": 2,
                })
                self.assertEqual(self.driver.execute_script(
                    "return visualViewport.scale"
                ), 2)
                self.driver.execute_cdp_cmd("Emulation.setPageScaleFactor", {
                    "pageScaleFactor": 1,
                })

        self.driver.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})
        self.driver.set_window_size(1440, 1000)
        self.assertEqual(self.driver.execute_script(
            "return getComputedStyle(document.querySelector('.tab-card tbody tr')).display"
        ), "table-row")
        self.driver.set_window_size(390, 844)
        self.driver.execute_cdp_cmd("Emulation.setEmulatedMedia", {"media": "print"})
        self.assertEqual(self.driver.execute_script(
            "return getComputedStyle(document.querySelector('.tab-card tbody tr')).display"
        ), "table-row")
