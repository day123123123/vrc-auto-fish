"""
输入控制模块
============
PostMessage — Win32 消息投递, 不移动光标不抢焦点 (鼠标)。
防卡杆动作通过 VRChat OSC API 发送 (不需要聚焦窗口)。
"""

import ctypes
import ctypes.wintypes
import time

from utils.logger import log

user32 = ctypes.windll.user32

WM_LBUTTONDOWN  = 0x0201
WM_LBUTTONUP    = 0x0202
WM_ACTIVATE     = 0x0006
WA_ACTIVE       = 1
MK_LBUTTON      = 0x0001


def _MAKELPARAM(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def _get_osc():
    """创建 OSC UDP 客户端 (VRChat 默认端口 9000)"""
    from pythonosc import udp_client
    return udp_client.SimpleUDPClient("127.0.0.1", 9000)


class InputController:
    """PostMessage 鼠标控制器 + OSC 防卡杆"""

    def __init__(self, window_mgr):
        self.wm = window_mgr
        self.mouse_is_down = False
        self._click_x = 400
        self._click_y = 400

    # ────────────────── 内部工具 ──────────────────

    def _update_click_pos(self):
        region = self.wm.get_region()
        if region:
            self._click_x = region[2] // 2
            self._click_y = region[3] // 2

    def _post(self, msg: int, wparam: int):
        hwnd = self.wm.hwnd
        if not hwnd:
            return False
        lparam = _MAKELPARAM(self._click_x, self._click_y)
        return bool(user32.PostMessageW(hwnd, msg, wparam, lparam))

    # ────────────────── 聚焦 ──────────────────

    def focus_game(self) -> bool:
        ok = self.wm.focus()
        if ok:
            self._update_click_pos()
        else:
            log.warning_t("input.focusGameFailed")
        return ok

    def move_to_game_center(self):
        self._update_click_pos()

    def ensure_cursor_in_game(self):
        pass

    # ────────────────── 鼠标操作 ──────────────────

    def click(self, focus: bool = False):
        if focus:
            self.focus_game()
            time.sleep(0.1)
        self._update_click_pos()
        self._post(WM_LBUTTONDOWN, MK_LBUTTON)
        time.sleep(0.06)
        self._post(WM_LBUTTONUP, 0)

    def click_rapid(self):
        self._post(WM_LBUTTONDOWN, MK_LBUTTON)
        time.sleep(0.02)
        self._post(WM_LBUTTONUP, 0)

    def mouse_down(self):
        if not self.mouse_is_down:
            self._post(WM_LBUTTONDOWN, MK_LBUTTON)
            self.mouse_is_down = True

    def mouse_up(self):
        if self.mouse_is_down:
            self._post(WM_LBUTTONUP, 0)
            self.mouse_is_down = False

    # ────────────────── 防卡杆: 摇头 (OSC LookLeft/LookRight) ──────────────────

    def _osc_reset_all(self, osc):
        """重置所有防卡杆相关的 OSC 信号，防止状态残留"""
        try:
            osc.send_message("/input/LookLeft", 0)
            osc.send_message("/input/LookRight", 0)
            osc.send_message("/input/Jump", 0)
        except Exception:
            pass

    def shake_head(self):
        """抛竿前摇头: 右→左，对称两步，通过 OSC。"""
        import config as _cfg
        t = getattr(_cfg, "SHAKE_HEAD_TIME", 0.01)
        if t <= 0:
            return
        try:
            osc = _get_osc()
        except ImportError:
            log.warning_t("input.shakeOscMissing")
            return
        except Exception as e:
            log.warning_t("input.shakeOscClientFailed", error=e)
            return
        try:
            self._osc_reset_all(osc)
            osc.send_message("/input/LookRight", 1)
            time.sleep(t)
            osc.send_message("/input/LookRight", 0)
            time.sleep(0.05)

            osc.send_message("/input/LookLeft", 1)
            time.sleep(t)
            osc.send_message("/input/LookLeft", 0)
            time.sleep(0.05)
        except Exception as e:
            log.warning_t("input.shakeOscSendFailed", error=e)

    # ────────────────── 防卡杆: 跳跃 (OSC /input/Jump) ──────────────────

    def jump_toggle(self):
        """跳跃防卡杆: 通过 OSC 发送 /input/Jump, 不需要聚焦窗口。"""
        try:
            osc = _get_osc()
        except ImportError:
            log.warning_t("input.jumpOscMissing")
            return
        except Exception as e:
            log.warning_t("input.jumpOscClientFailed", error=e)
            return
        try:
            self._osc_reset_all(osc)
            osc.send_message("/input/Jump", 1)
            time.sleep(0.05)
            osc.send_message("/input/Jump", 0)
            time.sleep(0.1)
        except Exception as e:
            log.warning_t("input.jumpOscSendFailed", error=e)

    # ────────────────── 安全 ──────────────────

    def safe_release(self):
        try:
            self._post(WM_LBUTTONUP, 0)
        except Exception:
            pass
        self.mouse_is_down = False
