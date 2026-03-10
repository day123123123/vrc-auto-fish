import os
import tempfile
import unittest
from types import SimpleNamespace
import json

import config
from gui.settings_store import AppSettingsStore


class FakeVar:
    def __init__(self, value=None):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeEntry:
    def __init__(self):
        self.states = []

    def state(self, state_value):
        self.states.append(tuple(state_value))


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.settings_file = os.path.join(self.tmpdir.name, "settings.json")
        self.old_settings_file = config.SETTINGS_FILE
        config.SETTINGS_FILE = self.settings_file
        self.log_messages = []
        self.app = SimpleNamespace(
            _param_vars={
                "HOLD_MIN_S": (FakeVar("30"), "ms"),
                "SUCCESS_PROGRESS": (FakeVar("60"), "pct"),
            },
            _param_entries={"SUCCESS_PROGRESS": FakeEntry()},
            PARAM_DEFAULTS={"HOLD_MIN_S": 0.025, "SUCCESS_PROGRESS": 0.55},
            SETTINGS_DEFAULTS={"SKIP_SUCCESS_CHECK": False, "SYNC_PD_MODE": True},
            PERSISTED_CONFIG_ATTRS=("SKIP_SUCCESS_CHECK", "SYNC_PD_MODE"),
            var_grouped_params=FakeVar(True),
            var_preset_name=FakeVar(""),
            var_skip_success=FakeVar(False),
            var_sync_pd_mode=FakeVar(True),
            var_anti_mode=FakeVar("jump"),
            var_shake_time=FakeVar("0.020"),
            _log_msg=self.log_messages.append,
            _update_success_threshold_state=lambda: None,
            _render_params_panel=lambda: None,
        )
        self.store = AppSettingsStore(self.app)
        self.old_hold_min = config.HOLD_MIN_S
        self.old_success_progress = config.SUCCESS_PROGRESS

    def tearDown(self):
        config.SETTINGS_FILE = self.old_settings_file
        config.HOLD_MIN_S = self.old_hold_min
        config.SUCCESS_PROGRESS = self.old_success_progress
        self.tmpdir.cleanup()

    def test_apply_params_updates_config_and_saves_file(self):
        self.store.apply_params()
        self.assertAlmostEqual(config.HOLD_MIN_S, 0.03)
        self.assertAlmostEqual(config.SUCCESS_PROGRESS, 0.6)
        self.assertTrue(os.path.exists(self.settings_file))
        with open(self.settings_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("current", data)

    def test_reset_params_restores_defaults(self):
        config.HOLD_MIN_S = 0.08
        config.SUCCESS_PROGRESS = 0.9
        self.store.save()

        self.store.reset_params()

        self.assertAlmostEqual(config.HOLD_MIN_S, 0.025)
        self.assertAlmostEqual(config.SUCCESS_PROGRESS, 0.55)
        self.assertTrue(os.path.exists(self.settings_file))

    def test_apply_loaded_setting_updates_widget_display(self):
        self.store.apply_loaded_setting("HOLD_MIN_S", 0.04)
        var, _ = self.app._param_vars["HOLD_MIN_S"]
        self.assertEqual(var.get(), "40")

    def test_save_and_load_preset(self):
        self.app.var_preset_name.set("测试预设")
        self.store.apply_params()
        self.store.save_preset("测试预设")

        self.app._param_vars["HOLD_MIN_S"][0].set("9")
        self.store.apply_params()
        self.assertAlmostEqual(config.HOLD_MIN_S, 0.009)

        loaded = self.store.load_preset("测试预设")
        self.assertTrue(loaded)
        self.assertAlmostEqual(config.HOLD_MIN_S, 0.03)
        self.assertEqual(self.store.get_active_preset_name(), "测试预设")

    def test_delete_preset(self):
        self.store.save_preset("预设A")
        self.assertIn("预设A", self.store.get_preset_names())
        deleted = self.store.delete_preset("预设A")
        self.assertTrue(deleted)
        self.assertNotIn("预设A", self.store.get_preset_names())


if __name__ == "__main__":
    unittest.main()
