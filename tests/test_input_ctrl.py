"""
input_ctrl 输入后端单元测试
==========================
用假的 user32 验证 SendInput 注入逻辑, 不会真的移动鼠标或点击。
"""

import ctypes
import unittest
from unittest.mock import patch

import config
from core.input_ctrl import (
    ABS_MAX,
    INPUT,
    INPUT_KEYBOARD,
    INPUT_MOUSE,
    MOUSEEVENTF_ABSOLUTE,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_MOVE,
    MOUSEEVENTF_VIRTUALDESK,
    WM_LBUTTONDOWN,
    WM_LBUTTONUP,
    InputController,
    _to_abs,
)

VX, VY, VW, VH = 0, 0, 1920, 1080        # 虚拟桌面
REGION = (100, 50, 800, 600)             # 窗口客户区: x, y, w, h
TARGET = (REGION[0] + REGION[2] // 2, REGION[1] + REGION[3] // 2)   # (500, 350)


def _screen_to_abs(value, origin, span):
    span = max(1, span - 1)
    return max(0, min(ABS_MAX, int(round((value - origin) / span * ABS_MAX))))


class FakeUser32:
    """记录所有注入事件, 并维护一个假的光标位置。"""

    def __init__(self, foreground=123, cursor=(0, 0), under=None):
        self.foreground = foreground
        self.cursor = cursor
        self.under = under           # WindowFromPoint 返回值 (None = 游戏窗口)
        self.mouse_events = []       # [(dx, dy, flags)]
        self.key_events = 0
        self.posted = []
        self.send_result = 1
        self.allow_foreground = True

    # ── SendInput / 输入 ──
    def SendInput(self, count, ptr, size):
        if self.send_result != 1:
            return self.send_result
        inp = ptr._obj
        if inp.type == INPUT_MOUSE:
            flags = inp.mi.dwFlags
            self.mouse_events.append((inp.mi.dx, inp.mi.dy, flags))
            if flags & MOUSEEVENTF_MOVE:
                self.cursor = (
                    round(inp.mi.dx / ABS_MAX * (VW - 1)) + VX,
                    round(inp.mi.dy / ABS_MAX * (VH - 1)) + VY,
                )
        elif inp.type == INPUT_KEYBOARD:
            self.key_events += 1
        return 1

    def GetForegroundWindow(self):
        return self.foreground

    def GetAncestor(self, hwnd, flag):
        return hwnd

    def GetCursorPos(self, ptr):
        ptr._obj.x, ptr._obj.y = self.cursor
        return 1

    def SetCursorPos(self, x, y):
        self.cursor = (int(x), int(y))
        return 1

    def GetSystemMetrics(self, index):
        return {76: VX, 77: VY, 78: VW, 79: VH}[index]

    def WindowFromPoint(self, point):
        return self.under if self.under is not None else self.foreground if self.foreground else 123

    def GetWindowTextLengthW(self, hwnd):
        return 0

    def GetWindowTextW(self, hwnd, buf, size):
        return 0

    def PostMessageW(self, hwnd, msg, wparam, lparam):
        self.posted.append((hwnd, msg, wparam, lparam))
        return 1

    def SetForegroundWindow(self, hwnd):
        if self.allow_foreground:
            self.foreground = hwnd
        return self.allow_foreground

    # ── 断言辅助 ──
    def flags(self):
        return [flags for _, _, flags in self.mouse_events]

    def moves(self):
        return [
            (dx, dy) for dx, dy, flags in self.mouse_events
            if flags & MOUSEEVENTF_MOVE
        ]


class FakeWindowManager:
    def __init__(self, hwnd=123, region=REGION, focus_result=True):
        self.hwnd = hwnd
        self.title = "VRChat"
        self.region = region
        self.focus_result = focus_result
        self.focus_calls = 0

    def get_region(self):
        return self.region

    def focus(self):
        self.focus_calls += 1
        return self.focus_result

    def is_valid(self):
        return self.hwnd is not None

    def is_foreground(self):
        return False


class InputControllerSendInputTests(unittest.TestCase):
    def setUp(self):
        self._old_mode = getattr(config, "INPUT_MODE", "sendinput")
        self._old_delay = getattr(config, "INPUT_FOCUS_DELAY", 0.08)
        self._old_restore = getattr(config, "INPUT_RESTORE_CURSOR", True)
        self._old_hold = getattr(config, "INPUT_CLICK_HOLD_S", 0.06)
        config.INPUT_MODE = "sendinput"
        config.INPUT_FOCUS_DELAY = 0.0
        config.INPUT_RESTORE_CURSOR = True
        config.INPUT_CLICK_HOLD_S = 0.0

    def tearDown(self):
        config.INPUT_MODE = self._old_mode
        config.INPUT_FOCUS_DELAY = self._old_delay
        config.INPUT_RESTORE_CURSOR = self._old_restore
        config.INPUT_CLICK_HOLD_S = self._old_hold

    def make(self, cursor=(0, 0), foreground=123, focus_result=True, under=None):
        fake = FakeUser32(foreground=foreground, cursor=cursor, under=under)
        wm = FakeWindowManager(focus_result=focus_result)
        controller = InputController(wm)
        patcher = patch("core.input_ctrl.user32", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return controller, fake, wm

    # ── 结构体 / 坐标 ──

    def test_input_struct_layout(self):
        expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(ctypes.sizeof(INPUT), expected)
        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp.mi.dx, inp.mi.dy, inp.mi.dwFlags = 1234, 5678, MOUSEEVENTF_LEFTDOWN
        self.assertEqual(inp.mi.dx, 1234)
        self.assertEqual(inp.mi.dy, 5678)
        self.assertEqual(inp.mi.dwFlags, MOUSEEVENTF_LEFTDOWN)

    def test_to_abs_normalization_edges_and_center(self):
        self.assertEqual(_to_abs(VX, VX, VW), 0)
        self.assertEqual(_to_abs(VX + VW - 1, VX, VW), ABS_MAX)
        # 1920 宽时中心像素是两个整数像素之间, 允许 ±20 的量化偏差
        self.assertAlmostEqual(_to_abs(VX + VW // 2, VX, VW), ABS_MAX // 2, delta=20)
        # 副屏在左侧时的负坐标原点
        self.assertEqual(_to_abs(-1920, -1920, 3840), 0)
        self.assertEqual(_to_abs(1920 - 1, -1920, 3840), ABS_MAX)

    # ── SendInput 点击 ──

    def test_click_moves_cursor_then_left_down_up(self):
        controller, fake, _ = self.make(cursor=(10, 10))
        controller.click()

        self.assertEqual(
            fake.flags(),
            [
                MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                MOUSEEVENTF_LEFTDOWN,
                MOUSEEVENTF_LEFTUP,
            ],
        )
        self.assertEqual(
            fake.moves()[0],
            (_screen_to_abs(TARGET[0], VX, VW), _screen_to_abs(TARGET[1], VY, VH)),
        )
        # 归一化往返后光标应落在窗口中心 (假 user32 会把绝对坐标还原成屏幕坐标)
        self.assertEqual(fake.cursor, TARGET)
        self.assertFalse(controller.mouse_is_down)

    def test_cursor_already_inside_game_is_not_moved(self):
        controller, fake, _ = self.make(cursor=TARGET)
        controller.click()
        self.assertEqual(fake.moves(), [])
        self.assertEqual(
            fake.flags(), [MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP]
        )

    def test_hold_cycle_moves_cursor_only_once(self):
        controller, fake, _ = self.make(cursor=(10, 10))
        for _ in range(3):
            controller.mouse_down()
            controller.mouse_up()

        self.assertEqual(len(fake.moves()), 1)
        self.assertEqual(fake.flags().count(MOUSEEVENTF_LEFTDOWN), 3)
        self.assertEqual(fake.flags().count(MOUSEEVENTF_LEFTUP), 3)

    def test_mouse_down_is_idempotent_while_held(self):
        controller, fake, _ = self.make(cursor=TARGET)
        controller.mouse_down()
        controller.mouse_down()
        self.assertEqual(fake.flags(), [MOUSEEVENTF_LEFTDOWN])
        self.assertTrue(controller.mouse_is_down)
        controller.mouse_up()
        controller.mouse_up()
        self.assertEqual(fake.flags(), [MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP])
        self.assertFalse(controller.mouse_is_down)

    def test_mouse_up_without_press_sends_nothing(self):
        controller, fake, _ = self.make()
        controller.mouse_up()
        controller.safe_release()
        self.assertEqual(fake.mouse_events, [])

    # ── 前台安全门 ──

    def test_click_skipped_when_game_cannot_be_focused(self):
        controller, fake, wm = self.make(foreground=999, focus_result=False)
        fake.allow_foreground = False

        controller.click()

        self.assertEqual(fake.mouse_events, [])
        self.assertEqual(fake.posted, [])
        self.assertTrue(wm.focus_calls >= 1)

    def test_click_proceeds_when_focus_is_recovered(self):
        controller, fake, wm = self.make(
            cursor=TARGET, foreground=999, focus_result=False)
        controller.click()
        self.assertEqual(fake.flags(), [MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP])
        self.assertTrue(wm.focus_calls >= 1)

    def test_skipped_when_window_region_missing(self):
        controller, fake, wm = self.make()
        wm.region = None
        controller.click()
        self.assertEqual(fake.mouse_events, [])

    def test_send_input_failure_keeps_state_consistent(self):
        controller, fake, _ = self.make(cursor=TARGET)
        fake.send_result = 0
        controller.mouse_down()
        self.assertFalse(controller.mouse_is_down)
        self.assertEqual(fake.flags(), [])

    # ── 遮挡检测 ──

    def test_click_skipped_when_click_point_is_covered(self):
        controller, fake, _ = self.make(cursor=(10, 10), under=999)
        controller.click()
        # 光标仍会被移到游戏内, 但按下/松开不应发出
        self.assertEqual(fake.flags().count(MOUSEEVENTF_LEFTDOWN), 0)
        self.assertEqual(fake.flags().count(MOUSEEVENTF_LEFTUP), 0)

    def test_click_resumes_after_covering_window_disappears(self):
        controller, fake, _ = self.make(cursor=(10, 10), under=999)
        controller.click()
        self.assertEqual(fake.flags().count(MOUSEEVENTF_LEFTDOWN), 0)

        fake.under = 123
        controller.click()
        self.assertEqual(fake.flags().count(MOUSEEVENTF_LEFTDOWN), 1)
        self.assertEqual(fake.flags().count(MOUSEEVENTF_LEFTUP), 1)

    # ── 光标还原 ──

    def test_restore_cursor_puts_cursor_back(self):
        controller, fake, _ = self.make(cursor=(10, 10))
        controller.click()
        self.assertEqual(fake.cursor, TARGET)

        controller.restore_cursor()
        self.assertEqual(fake.cursor, (10, 10))
        # 二次调用不会再次移动
        fake.mouse_events.clear()
        controller.restore_cursor()
        self.assertEqual(fake.mouse_events, [])

    def test_restore_cursor_respects_config_switch(self):
        config.INPUT_RESTORE_CURSOR = False
        controller, fake, _ = self.make(cursor=(10, 10))
        controller.click()
        controller.restore_cursor()
        self.assertEqual(fake.cursor, TARGET)


class InputControllerPostMessageTests(unittest.TestCase):
    """兼容后端: 旧版后台投递行为保持不变。"""

    def setUp(self):
        self._old_mode = getattr(config, "INPUT_MODE", "sendinput")
        config.INPUT_MODE = "postmessage"

    def tearDown(self):
        config.INPUT_MODE = self._old_mode

    def make(self):
        fake = FakeUser32(cursor=(0, 0))
        wm = FakeWindowManager()
        controller = InputController(wm)
        patcher = patch("core.input_ctrl.user32", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return controller, fake

    def test_click_posts_client_coordinates(self):
        controller, fake = self.make()
        self.assertFalse(controller.use_sendinput)

        controller.click()

        self.assertEqual(fake.mouse_events, [])
        messages = [(msg, wparam) for _, msg, wparam, _ in fake.posted]
        self.assertEqual(
            messages, [(WM_LBUTTONDOWN, 1), (WM_LBUTTONUP, 0)]
        )
        expected_lparam = ((REGION[3] // 2) << 16) | (REGION[2] // 2)
        self.assertEqual(fake.posted[0][3], expected_lparam)

    def test_unknown_mode_falls_back_to_sendinput(self):
        config.INPUT_MODE = "weird"
        controller, _ = self.make()
        self.assertTrue(controller.use_sendinput)


if __name__ == "__main__":
    unittest.main()
