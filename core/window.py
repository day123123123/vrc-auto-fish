"""
窗口管理模块
============
- 查找 VRChat 窗口（标题模糊匹配）
- DPI 感知 — 保证截图坐标与实际像素一致
- 智能聚焦 — 只在必要时切换前台
"""

import time
import ctypes
import ctypes.wintypes

from utils.logger import log

# ── DPI 感知（必须在任何窗口操作之前调用）──
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)          # Per-Monitor V2
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()           # Fallback
    except Exception:
        pass

# ── win32 常量 ──
SW_RESTORE = 9
GWL_STYLE = -16
WS_MINIMIZE = 0x20000000

user32 = ctypes.windll.user32


def _is_window(hwnd) -> bool:
    return bool(user32.IsWindow(hwnd))


def _is_iconic(hwnd) -> bool:
    return bool(user32.IsIconic(hwnd))


def _get_foreground() -> int:
    return user32.GetForegroundWindow()


class WindowManager:
    """VRChat 窗口管理器"""

    def __init__(self, title_keyword: str = "VRChat"):
        self.title_keyword = title_keyword
        self.hwnd = None
        self._title = ""
        self._rect = None       # (left, top, right, bottom)

    # ────────────────── 查找 ──────────────────

    # 排除自身窗口的关键字列表
    EXCLUDE_KEYWORDS = ["自动钓鱼", "auto-fish", "auto fish"]

    def find(self) -> bool:
        """
        枚举所有可见窗口，匹配标题包含关键字的窗口。
        自动排除脚本自身的 GUI 窗口。
        """
        results = []
        keyword_lower = self.title_keyword.lower()
        exclude = [kw.lower() for kw in self.EXCLUDE_KEYWORDS]

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        def enum_cb(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value
                    title_lower = title.lower()
                    # 必须包含关键字，且不能匹配到自身 GUI
                    if keyword_lower in title_lower:
                        if not any(ex in title_lower for ex in exclude):
                            results.append((hwnd, title))
            return True

        user32.EnumWindows(enum_cb, 0)

        if results:
            self.hwnd, self._title = results[0]
            self._update_rect()
            log.info_t("window.log.found", title=self._title, hwnd=self.hwnd)
            return True

        log.warning_t("window.log.notFoundByKeyword", keyword=self.title_keyword)
        self.hwnd = None
        return False

    # ────────────────── 聚焦 ──────────────────

    def focus(self) -> bool:
        """
        确保 VRChat 是前台窗口。
        如果已经是前台则直接返回 True，不做多余切换。
        """
        if not self.is_valid():
            if not self.find():
                return False

        # 已经是前台 → 不需要操作
        if _get_foreground() == self.hwnd:
            return True

        try:
            if _is_iconic(self.hwnd):
                user32.ShowWindow(self.hwnd, SW_RESTORE)
                time.sleep(0.15)

            # 方法1: SetForegroundWindow
            user32.SetForegroundWindow(self.hwnd)
            time.sleep(0.1)

            if _get_foreground() == self.hwnd:
                return True

            # 方法2: 附加线程后重试
            fg_hwnd = _get_foreground()
            fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
            my_tid = ctypes.windll.kernel32.GetCurrentThreadId()
            if fg_tid != my_tid:
                user32.AttachThreadInput(my_tid, fg_tid, True)
                user32.SetForegroundWindow(self.hwnd)
                user32.AttachThreadInput(my_tid, fg_tid, False)
                time.sleep(0.1)

            return _get_foreground() == self.hwnd

        except Exception as e:
            log.warning_t("window.log.focusFailed", error=e)
            return False

    # ────────────────── 区域 ──────────────────

    def get_region(self):
        """
        获取窗口在屏幕上的区域 (x, y, w, h)。
        使用 GetClientRect + ClientToScreen 获取纯客户区（无标题栏/边框）。
        """
        if not self.is_valid():
            if not self.find():
                return None

        try:
            # 客户区矩形 (相对于窗口左上角)
            rect = ctypes.wintypes.RECT()
            user32.GetClientRect(self.hwnd, ctypes.byref(rect))

            # 客户区左上角的屏幕坐标
            pt = ctypes.wintypes.POINT(0, 0)
            user32.ClientToScreen(self.hwnd, ctypes.byref(pt))

            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w > 0 and h > 0:
                return (pt.x, pt.y, w, h)
        except Exception:
            pass

        # Fallback: 用 GetWindowRect
        self._update_rect()
        if self._rect:
            l, t, r, b = self._rect
            if r - l > 0 and b - t > 0:
                return (l, t, r - l, b - t)
        return None

    # ────────────────── 状态 ──────────────────

    def is_valid(self) -> bool:
        return self.hwnd is not None and _is_window(self.hwnd)

    def is_foreground(self) -> bool:
        return self.is_valid() and _get_foreground() == self.hwnd

    @property
    def title(self) -> str:
        return self._title

    # ────────────────── 内部 ──────────────────

    def _update_rect(self):
        if self.hwnd and _is_window(self.hwnd):
            try:
                rect = ctypes.wintypes.RECT()
                user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
                self._rect = (rect.left, rect.top, rect.right, rect.bottom)
            except Exception:
                self._rect = None
