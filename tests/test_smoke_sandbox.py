import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

import config
from core.bot import FishingBot
from core.minigame_runtime import DetectionContext, MinigameRuntime, PipelineContext
from core.pd_controller import PDAction, PDController
from gui.app import FishingApp


class DummyThread:
    def join(self, timeout=None):
        return None


class DummyInput:
    def __init__(self):
        self.down_calls = 0
        self.up_calls = 0
        self.click_calls = 0
        self.safe_release_calls = 0

    def mouse_down(self):
        self.down_calls += 1

    def mouse_up(self):
        self.up_calls += 1

    def click(self, focus=False):
        self.click_calls += 1

    def safe_release(self):
        self.safe_release_calls += 1

    def ensure_cursor_in_game(self):
        return None

    def move_to_game_center(self):
        return None


class DummyWindow:
    def __init__(self):
        self.hwnd = 123
        self.title = "DummyVRChat"

    def is_valid(self):
        return True

    def find(self):
        return True


class DummyScreen:
    def save_debug(self, image, name="screenshot"):
        return None

    def reset_capture_method(self):
        return None


class DummyBotForGui:
    def __init__(self):
        self.running = False
        self.debug_mode = False
        self.fish_count = 0
        self.state = "就绪"
        self.yolo = object()
        self.window = DummyWindow()
        self.screen = DummyScreen()
        self.input = DummyInput()
        self.detector = SimpleNamespace(debug_report=False)

    def run(self):
        self.running = False

    def shutdown_debug_overlay(self):
        return None


def make_sandbox_bot():
    bot = FishingBot.__new__(FishingBot)
    bot.running = True
    bot.debug_mode = False
    bot.fish_count = 0
    bot.state = "就绪"
    bot.yolo = None
    bot.input = DummyInput()
    bot.screen = DummyScreen()
    bot.detector = SimpleNamespace(debug_report=False)
    bot.debug_overlay = SimpleNamespace(
        tick_fps=lambda: None,
        show=lambda *args, **kwargs: None,
        shutdown=lambda: None,
    )
    bot.pd = PDController()
    bot.il = SimpleNamespace(
        reset_round=lambda: None,
        record_frame=MagicMock(),
        policy=None,
        model_control=MagicMock(return_value=True),
        stop_recording=MagicMock(),
        load_policy=MagicMock(),
        start_recording=MagicMock(),
    )
    bot._auto_roi = None
    bot._need_rotation = False
    bot._track_angle = 0.0
    bot._bar_locked_cx = None
    bot._bar_smooth_cy = None
    bot._current_fish_name = ""
    bot._force_minigame = False
    bot._tick_fps = lambda: None
    bot._show_debug_overlay = lambda *args, **kwargs: None
    bot._wait_for_minigame_entry = lambda start, use_yolo: (True, True)
    bot._announce_minigame_start = lambda entered, use_yolo: None
    bot._initialize_minigame_context = (
        lambda ctx: (np.zeros((8, 8, 3), dtype=np.uint8), np.zeros((8, 8, 3), dtype=np.uint8))
    )
    bot._maybe_activate_minigame = lambda fish, bar, progress, runtime, ctx: "ok"
    bot._postprocess_minigame_detection = (
        lambda screen, screen_raw, fish, bar, mk, bs, yp, runtime, ctx: (fish, bar, yp)
    )
    bot._compute_minigame_progress = lambda screen, fish, bar, yp, runtime, ctx: 0.6
    bot._evaluate_minigame_end_state = lambda screen, fish, bar, runtime, rescue: "ok"
    bot._sync_pipeline_params = lambda runtime, ctx, pipe: None
    bot._log_minigame_frame = lambda fish, bar, green, runtime, skip: None
    bot._stop_pipeline = lambda pipe: None
    bot._wait_with_minigame_preempt = lambda *args, **kwargs: False
    bot._wait_until_ui_gone = lambda timeout=3.0, clear_frames=2: True
    bot._detect_minigame_ready_now = lambda screen: (False, None, None, None)
    return bot


class SmokeSandboxTests(unittest.TestCase):
    def test_gui_shell_smoke(self):
        try:
            import tkinter as tk
        except Exception as exc:
            self.skipTest(f"tkinter unavailable: {exc}")

        with patch("gui.app.FishingBot", DummyBotForGui), \
                patch("gui.app.keyboard.add_hotkey", lambda *args, **kwargs: None):
            root = tk.Tk()
            root.withdraw()
            try:
                app = FishingApp(root)
                app._apply_params = lambda: None
                app._on_start()
                app._on_stop()
                self.assertIsNotNone(app.btn_start)
                self.assertIsNotNone(app.var_state)
            finally:
                root.destroy()

    def test_sync_pd_smoke(self):
        bot = make_sandbox_bot()
        pipe = PipelineContext(sync_pd_mode=True)
        bot._start_pipeline = lambda ctx: pipe
        bot._grab = lambda: np.zeros((8, 8, 3), dtype=np.uint8)
        bot._detect_frame_once = lambda *args, **kwargs: (
            (1, 1, 2, 2, 0.9),
            (1, 1, 2, 4, 0.9),
            None,
            None,
            1.0,
            None,
            None,
        )
        bot._control_mouse = lambda fish, bar, sr: self._stop_after_press(bot)

        with patch.object(config, "USE_YOLO", False), \
                patch.object(config, "SYNC_PD_MODE", True), \
                patch.object(config, "IL_RECORD", False), \
                patch.object(config, "IL_USE_MODEL", False):
            result = bot._fishing_minigame(start_in_minigame=True)
        self.assertTrue(result)

    def test_async_pipeline_smoke(self):
        bot = make_sandbox_bot()
        result_q = queue.Queue()
        result_q.put((
            np.zeros((8, 8, 3), dtype=np.uint8),
            np.zeros((8, 8, 3), dtype=np.uint8),
            (1, 1, 2, 2, 0.9),
            (1, 1, 2, 4, 0.9),
            None,
            None,
            1.0,
            None,
        ))
        pipe = PipelineContext(
            sync_pd_mode=False,
            result_q=result_q,
            stop_evt=threading.Event(),
            shared_params={},
            params_lock=threading.Lock(),
            capture_thread=DummyThread(),
            detect_thread=DummyThread(),
        )
        bot._start_pipeline = lambda ctx: pipe
        bot._control_mouse = lambda fish, bar, sr: self._stop_after_press(bot)

        with patch.object(config, "USE_YOLO", False), \
                patch.object(config, "SYNC_PD_MODE", False), \
                patch.object(config, "IL_RECORD", False), \
                patch.object(config, "IL_USE_MODEL", False):
            result = bot._fishing_minigame(start_in_minigame=True)
        self.assertTrue(result)

    def test_il_path_smoke(self):
        bot = make_sandbox_bot()
        runtime = MinigameRuntime(frame=1)
        ctx = DetectionContext(use_yolo=False, skip_success_check=False)
        bot.il.policy = object()
        with patch.object(config, "IL_RECORD", False), \
                patch.object(config, "IL_USE_MODEL", True):
            held = bot._run_minigame_control(
                (1, 1, 2, 2, 0.9),
                (1, 1, 2, 4, 0.9),
                None,
                runtime,
                ctx,
            )
        self.assertTrue(held)
        self.assertTrue(bot.il.model_control.called)

    def test_finalize_minigame_smoke(self):
        bot = make_sandbox_bot()
        with patch.object(config, "IL_RECORD", False), \
                patch.object(config, "SKIP_SUCCESS_CHECK", False), \
                patch.object(config, "POST_CATCH_DELAY", 0.0), \
                patch.object(config, "SUCCESS_PROGRESS", 0.55):
            result = bot._finalize_minigame(
                hook_timeout_retry=False,
                skip_fish=False,
                skip_success_check=False,
                last_green=0.8,
            )
        self.assertTrue(result)
        self.assertEqual(bot.input.safe_release_calls, 1)
        self.assertEqual(bot.input.click_calls, 1)

    @staticmethod
    def _stop_after_press(bot):
        bot.running = False
        return True


if __name__ == "__main__":
    unittest.main()
