"""
输入控制模块
============
鼠标输入有两种后端 (config.INPUT_MODE):

1. **sendinput** (默认) — 通过 SendInput 注入系统级真实鼠标事件。
   U6 (Unity 6) 之后 VRChat 不再响应 PostMessage 投递的鼠标消息, 必须走真实输入通道。
   代价: 需要 VRChat 处于前台窗口, 且会占用真实光标 (脚本会把光标停在游戏窗口中心,
   停止后还原到接管前的位置)。

   ★ 光标搬移必须"静默"且发生在抢前台之前:
     - VRChat 桌面模式的鼠标视角会把光标位移当成鼠标移动, 一次跨屏搬移会被算成
       约 180° 的大甩视角 (26100301 首次 F9 的反馈);
     - 静默 = 只用 SetCursorPos (不产生 raw input 事件), 不用 SendInput 绝对移动;
     - 顺序 = 先搬光标再抢前台: 窗口不在前台时收不到 raw input, 位移不会被消费。

2. **postmessage** — 旧版后台消息投递, 不移动光标不抢焦点 (旧版本 VRChat 可用)。

安全设计: sendinput 模式下, 如果无法确认 VRChat 已在前台, **不注入任何事件**,
避免误点到用户的其他窗口。

防卡杆动作通过 VRChat OSC API 发送 (不需要聚焦窗口)。
"""

import ctypes
import ctypes.wintypes as wintypes
import time

import config
from utils.logger import log

user32 = ctypes.windll.user32

# ═══════════════════ PostMessage (兼容后端) ═══════════════════
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP   = 0x0202
MK_LBUTTON     = 0x0001

# ═══════════════════ SendInput ═══════════════════
INPUT_MOUSE    = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE        = 0x0001
MOUSEEVENTF_LEFTDOWN    = 0x0002
MOUSEEVENTF_LEFTUP      = 0x0004
MOUSEEVENTF_ABSOLUTE    = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

KEYEVENTF_KEYUP = 0x0002
VK_MENU         = 0x12          # Alt — 轻敲用于解除 SetForegroundWindow 限制

SM_XVIRTUALSCREEN  = 76
SM_YVIRTUALSCREEN  = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
GA_ROOT            = 2

ABS_MAX = 65535                 # SendInput 绝对坐标范围 0..65535
# 注: dwExtraInfo 保持 0 — 不给自己打标记, 注入事件与普通程序 (触控板/远程桌面)
# 的调用形式一致, 避免多余的特征。

_ULONG_PTR = (
    ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
)


# ────────────────── Win32 结构体 ──────────────────

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx",          wintypes.LONG),
        ("dy",          wintypes.LONG),
        ("mouseData",   wintypes.DWORD),
        ("dwFlags",     wintypes.DWORD),
        ("time",        wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",         wintypes.WORD),
        ("wScan",       wintypes.WORD),
        ("dwFlags",     wintypes.DWORD),
        ("time",        wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg",    wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u",    _INPUTUNION),
    ]


def _bind_sendinput():
    """为 SendInput 声明参数类型 (64 位下必须, 否则结构体长度会算错)。"""
    try:
        user32.SendInput.argtypes = (
            wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int,
        )
        user32.SendInput.restype = wintypes.UINT
    except Exception:
        pass


_bind_sendinput()


def _to_abs(value: int, origin: int, span: int) -> int:
    """屏幕坐标 → SendInput 绝对坐标 (虚拟桌面归一化到 0..65535)。"""
    span = max(1, int(span) - 1)
    ratio = (float(value) - float(origin)) / span
    return max(0, min(ABS_MAX, int(round(ratio * ABS_MAX))))


def _MAKELPARAM(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def _get_osc():
    """创建 OSC UDP 客户端 (VRChat 默认端口 9000)"""
    from pythonosc import udp_client
    return udp_client.SimpleUDPClient("127.0.0.1", 9000)


class InputController:
    """鼠标控制器: SendInput (默认) / PostMessage (兼容) + OSC 防卡杆"""

    def __init__(self, window_mgr):
        self.wm = window_mgr
        self.mouse_is_down = False

        # 点击目标
        self._click_x = 400          # 客户区坐标 (PostMessage 用)
        self._click_y = 400
        self._screen_x = 400         # 屏幕坐标 (SendInput 用)
        self._screen_y = 400
        self._client_rect = None     # 客户区屏幕矩形 (left, top, right, bottom)

        # 光标接管状态
        self._saved_cursor = None    # 接管前用户光标位置
        self._cursor_owned = False

        # 故障日志抑制 (避免每帧刷屏)
        self._focus_warned = False
        self._inject_warned = False
        self._cover_warned = False

    # ────────────────── 后端选择 ──────────────────

    @property
    def use_sendinput(self) -> bool:
        """当前是否使用 SendInput 后端 (其余取值一律按 SendInput 处理)。"""
        mode = str(getattr(config, "INPUT_MODE", "sendinput")).strip().lower()
        return mode != "postmessage"

    # ────────────────── 内部工具 ──────────────────

    def _update_click_pos(self) -> bool:
        """刷新点击坐标; 返回是否拿到了窗口区域。"""
        region = self.wm.get_region()
        if not region:
            return False
        rx, ry, rw, rh = region
        if rw <= 0 or rh <= 0:
            return False
        cx, cy = rw // 2, rh // 2
        self._click_x, self._click_y = cx, cy                # 客户区坐标
        self._screen_x, self._screen_y = rx + cx, ry + cy    # 屏幕坐标
        self._client_rect = (rx, ry, rx + rw, ry + rh)
        return True

    def _post(self, msg: int, wparam: int) -> bool:
        hwnd = self.wm.hwnd
        if not hwnd:
            return False
        lparam = _MAKELPARAM(self._click_x, self._click_y)
        return bool(user32.PostMessageW(hwnd, msg, wparam, lparam))

    # ────────────────── SendInput 底层 ──────────────────

    def _warn_inject_failed(self, error: int):
        if not self._inject_warned:
            self._inject_warned = True
            log.warning_t("input.sendInputFailed", error=error)

    def _send_mouse(self, flags: int, dx: int = 0, dy: int = 0) -> bool:
        """注入一个鼠标事件 (坐标仅在绝对移动时有效)。"""
        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp.mi = MOUSEINPUT(int(dx), int(dy), 0, int(flags), 0, 0)
        sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        if sent != 1:
            self._warn_inject_failed(int(ctypes.windll.kernel32.GetLastError()))
            return False
        self._inject_warned = False
        return True

    def _send_key_tap(self, vk: int):
        """轻敲一次按键 (SendInput), 用于解除前台激活限制。"""
        for flags in (0, KEYEVENTF_KEYUP):
            inp = INPUT()
            inp.type = INPUT_KEYBOARD
            inp.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def _virtual_desktop(self):
        return (
            user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
        )

    def _quiet_cursor_move(self) -> bool:
        """是否使用静默搬移 (SetCursorPos) 而不是 SendInput 绝对移动。"""
        return bool(getattr(config, "INPUT_QUIET_CURSOR_MOVE", True))

    def _move_cursor(self, x: int, y: int) -> bool:
        """
        把真实光标搬到屏幕坐标 (x, y) — 静默优先。

        默认用 SetCursorPos: 它只更新光标位置, 不产生 raw input 事件, 因此不会被
        VRChat 的鼠标视角当成鼠标位移 (SendInput 绝对移动会被当成位移 → 甩视角)。
        只有在 SetCursorPos 失败时才退回到 SendInput 绝对移动。
        """
        x, y = int(x), int(y)
        if self._quiet_cursor_move():
            try:
                if user32.SetCursorPos(x, y):
                    return True
            except Exception:
                pass
        # 退路: SendInput 绝对移动 (会产生 raw input 位移, 可能被游戏算成视角转动)
        vx, vy, vw, vh = self._virtual_desktop()
        ok = self._send_mouse(
            MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
            _to_abs(x, vx, vw), _to_abs(y, vy, vh),
        )
        if not ok:
            try:
                user32.SetCursorPos(x, y)
            except Exception:
                pass
        return ok

    def _cursor_pos(self):
        pt = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(pt)):
            return None
        return (int(pt.x), int(pt.y))

    def _cursor_in_game(self) -> bool:
        if not self._client_rect:
            return False
        pos = self._cursor_pos()
        if pos is None:
            return False
        left, top, right, bottom = self._client_rect
        return left <= pos[0] < right and top <= pos[1] < bottom

    def _window_title(self, hwnd) -> str:
        try:
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value or f"HWND={hwnd}"
        except Exception:
            return f"HWND={hwnd}"

    def _cursor_over_game(self) -> bool:
        """
        光标下方是否是游戏窗口。

        SendInput 的点击落在光标下方的窗口上, 如果中间被别的窗口 (例如本程序的
        debug 窗口或其他置顶窗口) 盖住, 点击不会作用到游戏, 需要提示用户。
        """
        hwnd = self.wm.hwnd
        pos = self._cursor_pos()
        if not hwnd or pos is None:
            return True
        try:
            under = user32.WindowFromPoint(wintypes.POINT(int(pos[0]), int(pos[1])))
        except Exception:
            return True
        if not under:
            return True
        if under == hwnd:
            return True
        try:
            if user32.GetAncestor(under, GA_ROOT) == hwnd:
                return True
        except Exception:
            return True
        if not self._cover_warned:
            self._cover_warned = True
            log.warning_t("input.cursorCovered", title=self._window_title(under))
        return False

    # ────────────────── 前台 / 光标准备 ──────────────────

    def _is_foreground(self) -> bool:
        hwnd = self.wm.hwnd
        if not hwnd:
            return False
        fg = user32.GetForegroundWindow()
        if not fg:
            return False
        if fg == hwnd:
            return True
        try:
            return user32.GetAncestor(fg, GA_ROOT) == hwnd
        except Exception:
            return False

    def _ensure_foreground(self) -> bool:
        """确保 VRChat 是前台窗口 (SendInput 生效的前提)。"""
        if self._is_foreground():
            self._focus_warned = False
            return True

        self.wm.focus()                      # 内部含 SetForegroundWindow + AttachThreadInput
        if self._is_foreground():
            self._focus_warned = False
            return True

        # Alt 轻敲: 绕过 Windows 的前台锁定限制后再试一次
        self._send_key_tap(VK_MENU)
        hwnd = self.wm.hwnd
        if hwnd:
            user32.SetForegroundWindow(hwnd)
        time.sleep(self._focus_delay())
        if self._is_foreground():
            self._focus_warned = False
            return True

        if not self._focus_warned:
            self._focus_warned = True
            log.warning_t("input.foregroundRequired")
        return False

    @staticmethod
    def _focus_delay() -> float:
        try:
            return max(0.0, float(getattr(config, "INPUT_FOCUS_DELAY", 0.08)))
        except (TypeError, ValueError):
            return 0.08

    def _remember_cursor(self):
        """首次接管光标前记录用户原位置。"""
        if self._saved_cursor is not None:
            return
        if not getattr(config, "INPUT_RESTORE_CURSOR", True):
            return
        pos = self._cursor_pos()
        if pos is not None:
            self._saved_cursor = pos

    def _park_cursor(self):
        """
        必要时把光标静默搬进游戏窗口 (已经在窗口内就一点都不搬)。

        搬移动静默进行, 且调用方必须保证在抢前台之前调用它, 否则游戏会把这
        段位移当成鼠标移动 (见模块开头的说明)。
        """
        if not self._client_rect:
            return
        if self._cursor_in_game():
            return
        self._remember_cursor()
        self._move_cursor(self._screen_x, self._screen_y)
        self._cursor_owned = True

    def _prepare_sendinput(self) -> bool:
        """注入前准备: 窗口区域 + 光标就位 + 前台。任一失败都不注入。"""
        if not self._update_click_pos():
            log.warning_t("input.noWindowRegion")
            return False

        # ★ 顺序关键: 先搬光标, 再抢前台。
        #   窗口不在前台时收不到 raw input, 这段位移不会被鼠标视角消费,
        #   因此不会出现"首次启动甩视角"的问题。
        self._park_cursor()

        if not self._ensure_foreground():
            return False

        # 抢前台可能把窗口从最小化恢复出来 (位置会变), 重新取一次区域;
        # 若光标仍不在窗口内, 再静默补搬一次。
        self._update_click_pos()
        self._park_cursor()

        if not self._cursor_over_game():
            return False          # 遮挡警告已在 _cursor_over_game 内记录
        self._cover_warned = False
        return True

    def restore_cursor(self):
        """把光标还原到脚本接管前的位置 (停止钓鱼时调用)。"""
        pos, self._saved_cursor = self._saved_cursor, None
        owned, self._cursor_owned = self._cursor_owned, False
        if pos is None or not owned or not self.use_sendinput:
            return
        try:
            self._move_cursor(pos[0], pos[1])
        except Exception:
            pass

    # ────────────────── 聚焦 ──────────────────

    def focus_game(self) -> bool:
        ok = self.wm.focus()
        if ok:
            self._update_click_pos()
        else:
            log.warning_t("input.focusGameFailed")
        return ok

    def move_to_game_center(self):
        """把点击目标设在游戏窗口中心 (光标不在窗口内时才静默搬动)。"""
        if not self._update_click_pos():
            return
        if not self.use_sendinput:
            return
        self._park_cursor()

    def ensure_cursor_in_game(self):
        """确保光标停在游戏窗口内 (SendInput 后才需要)。"""
        if not self.use_sendinput:
            return
        self._prepare_sendinput()

    # ────────────────── 鼠标操作 ──────────────────

    def _click_hold_s(self) -> float:
        try:
            return max(0.0, float(getattr(config, "INPUT_CLICK_HOLD_S", 0.06)))
        except (TypeError, ValueError):
            return 0.06

    def click(self, focus: bool = False):
        """单击左键 (SendInput 模式下自动保证前台与光标位置)。"""
        if self.use_sendinput:
            if not self._prepare_sendinput():
                return
            self._send_mouse(MOUSEEVENTF_LEFTDOWN)
            time.sleep(self._click_hold_s())
            self._send_mouse(MOUSEEVENTF_LEFTUP)
            return

        if focus:
            self.focus_game()
            time.sleep(0.1)
        self._update_click_pos()
        self._post(WM_LBUTTONDOWN, MK_LBUTTON)
        time.sleep(0.06)
        self._post(WM_LBUTTONUP, 0)

    def click_rapid(self):
        """快速连点 (小游戏收尾用)。"""
        if self.use_sendinput:
            if not self._prepare_sendinput():
                return
            self._send_mouse(MOUSEEVENTF_LEFTDOWN)
            time.sleep(0.02)
            self._send_mouse(MOUSEEVENTF_LEFTUP)
            return

        self._post(WM_LBUTTONDOWN, MK_LBUTTON)
        time.sleep(0.02)
        self._post(WM_LBUTTONUP, 0)

    def mouse_down(self):
        if self.mouse_is_down:
            return
        if self.use_sendinput:
            if not self._prepare_sendinput():
                return
            if self._send_mouse(MOUSEEVENTF_LEFTDOWN):
                self.mouse_is_down = True
            return
        self._post(WM_LBUTTONDOWN, MK_LBUTTON)
        self.mouse_is_down = True

    def mouse_up(self):
        if not self.mouse_is_down:
            return
        if self.use_sendinput:
            # 释放不需要重新定位/聚焦, 避免按住期间多余的光标移动
            self._send_mouse(MOUSEEVENTF_LEFTUP)
        else:
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
        """松开左键 (只在脚本认为自己按住时发送, 避免影响用户自己的拖拽)。"""
        try:
            if self.mouse_is_down:
                if self.use_sendinput:
                    self._send_mouse(MOUSEEVENTF_LEFTUP)
                else:
                    self._post(WM_LBUTTONUP, 0)
        except Exception:
            pass
        self.mouse_is_down = False
