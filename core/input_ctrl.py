"""
输入控制模块
============
支持两种输入模式:

1. PostMessage (默认) — Win32 消息投递, 不移动光标不抢焦点
2. OSC — 通过 VRChat OSC API 发送 /input/UseRight, 更稳定且不占用鼠标

通过 use_osc 参数切换。
"""

import ctypes
import ctypes.wintypes
import time

from utils.logger import log

user32 = ctypes.windll.user32

# ═══════════════════ Win32 消息常量 ═══════════════════

WM_LBUTTONDOWN  = 0x0201
WM_LBUTTONUP    = 0x0202
WM_ACTIVATE     = 0x0006
WA_ACTIVE       = 1
MK_LBUTTON      = 0x0001


def _MAKELPARAM(x: int, y: int) -> int:
    """将 (x, y) 打包为 lParam"""
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


# ═══════════════════ 控制器 ═══════════════════

class InputController:
    """
    鼠标输入控制器 — 支持 PostMessage / OSC 双模式

    PostMessage: 基于 Win32 窗口消息, 不移动系统光标
    OSC:         通过 VRChat OSC API (/input/UseRight) 发送, 不占用鼠标
    """

    def __init__(self, window_mgr, use_osc: bool = False):
        self.wm = window_mgr
        self.mouse_is_down = False
        self._click_x = 400
        self._click_y = 400

        self._use_osc = False
        self._osc_client = None
        if use_osc:
            self._init_osc()

    # ────────────────── OSC 初始化 ──────────────────

    def _init_osc(self):
        try:
            from pythonosc import udp_client
            self._osc_client = udp_client.SimpleUDPClient("127.0.0.1", 9000)
            self._osc_client.send_message("/input/UseRight", 0)
            self._use_osc = True
            log.info("[输入] OSC 模式已启用 (→ 127.0.0.1:9000)")
        except ImportError:
            log.warning("[输入] python-osc 未安装 (pip install python-osc), 回退到 PostMessage")
        except Exception as e:
            log.warning(f"[输入] OSC 初始化失败: {e}, 回退到 PostMessage")

    def _osc_send(self, value: int):
        if self._osc_client:
            try:
                self._osc_client.send_message("/input/UseRight", value)
            except Exception:
                pass

    # ────────────────── 内部工具 (PostMessage) ──────────────────

    def _update_click_pos(self):
        """计算窗口客户区中心坐标 (相对于客户区左上角)"""
        region = self.wm.get_region()
        if region:
            self._click_x = region[2] // 2
            self._click_y = region[3] // 2

    def _post(self, msg: int, wparam: int):
        """通过 PostMessage 向 VRChat 窗口发送鼠标消息"""
        hwnd = self.wm.hwnd
        if not hwnd:
            return False
        lparam = _MAKELPARAM(self._click_x, self._click_y)
        return bool(user32.PostMessageW(hwnd, msg, wparam, lparam))

    # ────────────────── 聚焦 (仅初始需要) ──────────────────

    def focus_game(self) -> bool:
        """聚焦 VRChat 窗口 — 仅在开始时调用一次。"""
        ok = self.wm.focus()
        if ok:
            self._update_click_pos()
        else:
            log.warning("无法聚焦 VRChat 窗口")
        return ok

    def move_to_game_center(self):
        """[兼容接口] OSC 模式不需要, PostMessage 模式更新坐标"""
        if not self._use_osc:
            self._update_click_pos()

    def ensure_cursor_in_game(self):
        """[兼容接口] 两种模式都不需要检查光标位置"""
        pass

    # ────────────────── 鼠标操作 ──────────────────

    def click(self, focus: bool = False):
        """鼠标左键单击"""
        if self._use_osc:
            log.info(f"[输入] click(OSC): UseRight 1→0")
            self._osc_send(1)
            time.sleep(0.12)
            self._osc_send(0)
            return

        if focus:
            self.focus_game()
            time.sleep(0.1)
        self._update_click_pos()
        log.info(f"[输入] click(PM): hwnd={self.wm.hwnd} pos=({self._click_x},{self._click_y})")
        self._post(WM_LBUTTONDOWN, MK_LBUTTON)
        time.sleep(0.12)
        self._post(WM_LBUTTONUP, 0)

    def click_rapid(self):
        """快速点击 — 按下+释放"""
        if self._use_osc:
            self._osc_send(1)
            time.sleep(0.02)
            self._osc_send(0)
            return

        self._post(WM_LBUTTONDOWN, MK_LBUTTON)
        time.sleep(0.02)
        self._post(WM_LBUTTONUP, 0)

    def mouse_down(self):
        """按下左键（小游戏：白条上升）"""
        if not self.mouse_is_down:
            if self._use_osc:
                self._osc_send(1)
            else:
                self._post(WM_LBUTTONDOWN, MK_LBUTTON)
            self.mouse_is_down = True

    def mouse_up(self):
        """释放左键（小游戏：白条下降）"""
        if self.mouse_is_down:
            if self._use_osc:
                self._osc_send(0)
            else:
                self._post(WM_LBUTTONUP, 0)
            self.mouse_is_down = False

    # ────────────────── 视角控制 ──────────────────

    def shake_head(self):
        """
        抛竿前摇头: 右→左，对称两步。
        按住时长从 config.SHAKE_HEAD_TIME 读取, 0 则跳过。
        始终通过 OSC 发送 (独立于输入模式), VRChat 需开启 OSC。
        """
        import config as _cfg
        t = getattr(_cfg, "SHAKE_HEAD_TIME", 0.01)
        if t <= 0:
            return
        try:
            from pythonosc import udp_client
            osc = udp_client.SimpleUDPClient("127.0.0.1", 9000)
        except Exception:
            return
        try:
            osc.send_message("/input/LookRight", 1)
            time.sleep(t)
            osc.send_message("/input/LookRight", 0)
            time.sleep(0.05)

            osc.send_message("/input/LookLeft", 1)
            time.sleep(t)
            osc.send_message("/input/LookLeft", 0)
            time.sleep(0.05)
        except Exception:
            pass

    # ────────────────── 安全 ──────────────────

    def safe_release(self):
        """安全释放"""
        try:
            if self._use_osc:
                self._osc_send(0)
            else:
                self._post(WM_LBUTTONUP, 0)
        except Exception:
            pass
        self.mouse_is_down = False

    @staticmethod
    def check_failsafe():
        """检测鼠标是否在左上角（安全中断）"""
        pt = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x <= 5 and pt.y <= 5
