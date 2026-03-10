import unittest
from unittest.mock import patch

from core.pd_controller import PDController


class PDControllerTests(unittest.TestCase):
    def setUp(self):
        self.controller = PDController()

    def test_decide_with_fish_and_bar_presses_when_fish_above_center(self):
        action = self.controller.decide(
            fish=(0, 10, 10, 10, 0.9),
            bar=(0, 0, 10, 40, 0.9),
            search_region=(0, 0, 100, 100),
            current_fish_name="fish_blue",
            detect_roi=None,
        )
        self.assertTrue(action.should_press)
        self.assertGreater(action.hold_s, 0.0)

    def test_decide_with_fish_and_bar_releases_when_fish_below_center(self):
        action = self.controller.decide(
            fish=(0, 45, 10, 10, 0.9),
            bar=(0, 0, 10, 40, 0.9),
            search_region=(0, 0, 100, 100),
            current_fish_name="fish_blue",
            detect_roi=None,
        )
        self.assertFalse(action.should_press)

    def test_decide_with_only_fish_uses_search_region_midline(self):
        action = self.controller.decide(
            fish=(0, 10, 10, 10, 0.9),
            bar=None,
            search_region=(0, 0, 100, 100),
            current_fish_name="fish_blue",
            detect_roi=None,
        )
        self.assertTrue(action.should_press)

    def test_decide_with_only_bar_and_no_history_releases(self):
        action = self.controller.decide(
            fish=None,
            bar=(0, 10, 10, 40, 0.9),
            search_region=(0, 0, 100, 100),
            current_fish_name="fish_blue",
            detect_roi=None,
        )
        self.assertFalse(action.should_press)

    def test_decide_with_only_bar_and_fish_history_can_press(self):
        self.controller.last_fish_cy = 5
        action = self.controller.decide(
            fish=None,
            bar=(0, 20, 10, 40, 0.9),
            search_region=(0, 0, 100, 100),
            current_fish_name="fish_blue",
            detect_roi=None,
        )
        self.assertTrue(action.should_press)

    def test_min_hold_is_respected_when_configured(self):
        with patch("config.MIN_HOLD_S", 0.02, create=True):
            action = self.controller.decide(
                fish=(0, -5, 10, 10, 0.9),
                bar=(0, 0, 10, 40, 0.9),
                search_region=(0, 0, 100, 100),
                current_fish_name="fish_blue",
                detect_roi=None,
            )
        self.assertTrue(action.should_press)
        self.assertGreaterEqual(action.hold_s, 0.02)


if __name__ == "__main__":
    unittest.main()
