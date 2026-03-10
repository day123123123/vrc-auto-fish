import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import config
from gui.runtime_controller import AppRuntimeController


class FakeButton:
    def __init__(self):
        self.state = None

    def config(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]


class FakeRoot:
    def __init__(self):
        self.after_calls = []
        self.destroyed = False

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))

    def destroy(self):
        self.destroyed = True

    def wm_attributes(self, *_args, **_kwargs):
        return None

    def lift(self):
        return None

    def focus_force(self):
        return None


class FakeVar:
    def __init__(self, value=None):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class RuntimeControllerTests(unittest.TestCase):
    def make_app(self):
        root = FakeRoot()
        logs = []
        bot = SimpleNamespace(
            running=False,
            state="就绪",
            _force_minigame=False,
            window=SimpleNamespace(
                is_valid=lambda: True,
                find=lambda: True,
                hwnd=123,
                title="VRChat",
            ),
            input=SimpleNamespace(safe_release=lambda: None),
            screen=SimpleNamespace(reset_capture_method=lambda: None),
            shutdown_debug_overlay=lambda: None,
            run=lambda: None,
            fish_count=0,
            debug_mode=False,
        )
        app = SimpleNamespace(
            root=root,
            bot=bot,
            bot_thread=None,
            var_window=FakeVar("未连接"),
            var_state=FakeVar("就绪"),
            var_count=FakeVar("0"),
            var_yolo_status=FakeVar(""),
            lbl_state=SimpleNamespace(config=lambda **kwargs: None),
            btn_start=FakeButton(),
            btn_stop=FakeButton(),
            btn_roi=FakeButton(),
            btn_clear_roi=FakeButton(),
            _apply_params=lambda: None,
            _log_msg=logs.append,
        )
        return app, logs

    def test_on_start_marks_bot_running_and_starts_thread(self):
        app, logs = self.make_app()
        controller = AppRuntimeController(app)

        with patch.object(AppRuntimeController, "has_non_ascii", return_value=False):
            controller.on_start()

        self.assertTrue(app.bot.running)
        self.assertEqual(app.btn_start.state, "disabled")
        self.assertEqual(app.btn_stop.state, "normal")
        self.assertTrue(any("开始自动钓鱼" in msg for msg in logs))

    def test_on_stop_releases_input_and_resets_buttons(self):
        app, logs = self.make_app()
        releases = {"count": 0}
        app.bot.running = True
        app.bot.input = SimpleNamespace(
            safe_release=lambda: releases.__setitem__("count", releases["count"] + 1)
        )
        app.bot.shutdown_debug_overlay = lambda: None
        controller = AppRuntimeController(app)

        controller.on_stop()

        self.assertFalse(app.bot.running)
        self.assertEqual(releases["count"], 1)
        self.assertEqual(app.btn_start.state, "normal")
        self.assertTrue(any("已停止" in msg for msg in logs))

    def test_update_yolo_status_sets_summary(self):
        app, _logs = self.make_app()
        controller = AppRuntimeController(app)
        with patch("os.path.exists", return_value=True), \
                patch("os.path.isdir", return_value=False):
            controller.update_yolo_status()
        self.assertIn("模型", app.var_yolo_status.get())


if __name__ == "__main__":
    unittest.main()
