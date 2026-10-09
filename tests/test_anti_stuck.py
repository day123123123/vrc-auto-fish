"""
防卡杆模式单元测试
==================
覆盖 "shake" / "jump" / "none"(关闭) 三种模式在抛竿阶段的行为,
以及设置存储对 "none" 及其兼容写法的处理。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import config
from core.bot import FishingBot
from gui.settings_store import AppSettingsStore


class RecordingInput:
    """记录防卡杆/点击调用的假输入控制器。"""

    def __init__(self):
        self.calls = []

    def click(self, focus=False):
        self.calls.append("click")

    def jump_toggle(self):
        self.calls.append("jump")

    def shake_head(self):
        self.calls.append("shake")

    def mouse_up(self):
        self.calls.append("mouse_up")


class FakeVar:
    def __init__(self, value=None):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


def make_cast_bot():
    """构造一个只够跑 _cast_rod 的最小 bot。"""
    bot = FishingBot.__new__(FishingBot)
    bot.state = ""
    bot.running = True
    bot.input = RecordingInput()
    bot._wait_with_minigame_preempt = lambda *args, **kwargs: False
    bot._grab = lambda: None
    bot._tick_fps = lambda: None
    bot._show_debug_overlay = lambda *args, **kwargs: None
    return bot


class AntiStuckModeTests(unittest.TestCase):
    def setUp(self):
        self._old_mode = config.ANTI_STUCK_MODE

    def tearDown(self):
        config.ANTI_STUCK_MODE = self._old_mode

    def cast(self, mode, use_attr=True):
        bot = make_cast_bot()
        if use_attr:
            config.ANTI_STUCK_MODE = mode
            with patch.object(config, "IL_RECORD", False), \
                    patch.object(config, "CAST_DELAY", 0.0):
                FishingBot._cast_rod(bot)
        else:
            with patch.object(config, "IL_RECORD", False), \
                    patch.object(config, "CAST_DELAY", 0.0), \
                    patch("core.bot.getattr", lambda *a, **k: "jump"):
                FishingBot._cast_rod(bot)
        return bot.input.calls

    def test_jump_mode_only_jumps(self):
        calls = self.cast("jump")
        self.assertIn("click", calls)          # 抛竿点击照常
        self.assertIn("jump", calls)
        self.assertNotIn("shake", calls)

    def test_shake_mode_only_shakes(self):
        calls = self.cast("shake")
        self.assertIn("click", calls)
        self.assertIn("shake", calls)
        self.assertNotIn("jump", calls)

    def test_none_mode_does_neither(self):
        """关闭防卡杆: 既不跳跃也不摇头, 但抛竿点击正常。"""
        calls = self.cast("none")
        self.assertIn("click", calls)
        self.assertNotIn("jump", calls)
        self.assertNotIn("shake", calls)

    def test_off_alias_also_disables(self):
        calls = self.cast("off")
        self.assertNotIn("jump", calls)
        self.assertNotIn("shake", calls)

    def test_case_and_space_insensitive(self):
        calls = self.cast("  NONE  ")
        self.assertNotIn("jump", calls)
        self.assertNotIn("shake", calls)

    def test_unknown_mode_falls_back_to_jump(self):
        calls = self.cast("bogus")
        self.assertIn("jump", calls)
        self.assertNotIn("shake", calls)

    def test_empty_mode_falls_back_to_jump(self):
        calls = self.cast("")
        self.assertIn("jump", calls)

    def test_record_mode_skips_anti_stuck(self):
        bot = make_cast_bot()
        config.ANTI_STUCK_MODE = "jump"
        with patch.object(config, "IL_RECORD", True), \
                patch.object(config, "CAST_DELAY", 0.0):
            FishingBot._cast_rod(bot)
        self.assertEqual(bot.input.calls, [])


class AntiStuckSettingStoreTests(unittest.TestCase):
    def setUp(self):
        self._old_mode = config.ANTI_STUCK_MODE
        self.var_anti_mode = FakeVar("jump")
        self.app = SimpleNamespace(var_anti_mode=self.var_anti_mode)
        self.store = AppSettingsStore(self.app)

    def tearDown(self):
        config.ANTI_STUCK_MODE = self._old_mode

    def test_apply_loaded_setting_accepts_none(self):
        self.assertTrue(self.store.apply_loaded_setting("ANTI_STUCK_MODE", "none"))
        self.assertEqual(config.ANTI_STUCK_MODE, "none")
        self.assertEqual(self.var_anti_mode.get(), "none")

    def test_apply_loaded_setting_rejects_unknown_value(self):
        config.ANTI_STUCK_MODE = "jump"
        self.assertTrue(self.store.apply_loaded_setting("ANTI_STUCK_MODE", "bogus"))
        self.assertEqual(config.ANTI_STUCK_MODE, "jump")

    def test_normalize_maps_hand_written_aliases_to_none(self):
        for raw in ("off", "OFF", "disable", "disabled", "false", "关闭"):
            data = {"ANTI_STUCK_MODE": raw}
            self.store.normalize_loaded_settings(data)
            self.assertEqual(data["ANTI_STUCK_MODE"], "none", raw)

    def test_normalize_keeps_legacy_crouch_mapping(self):
        data = {"ANTI_STUCK_MODE": "crouch"}
        self.store.normalize_loaded_settings(data)
        self.assertEqual(data["ANTI_STUCK_MODE"], "jump")

    def test_valid_modes_are_untouched(self):
        for raw in ("shake", "jump", "none"):
            data = {"ANTI_STUCK_MODE": raw}
            self.store.normalize_loaded_settings(data)
            self.assertEqual(data["ANTI_STUCK_MODE"], raw)


if __name__ == "__main__":
    unittest.main()
