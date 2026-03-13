"""
ROI Overlay
===========
在游戏窗口上叠加一个透明、点击穿透、置顶的矩形框,
实时标示 DETECT_ROI 的屏幕位置。

- WS_EX_LAYERED + LWA_COLORKEY  → 背景透明
- WS_EX_TRANSPARENT             → 鼠标点击穿透
- WS_EX_TOPMOST                 → 始终置顶
- WS_EX_TOOLWINDOW              → 不出现在任务栏
"""

import threading
import ctypes
import ctypes.wintypes

import config
from utils.logger import log

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

# ── Win32 常量 ──
WS_POPUP           = 0x80000000
WS_EX_LAYERED      = 0x00080000
WS_EX_TRANSPARENT  = 0x00000020
WS_EX_TOPMOST      = 0x00000008
WS_EX_TOOLWINDOW   = 0x00000080
WS_EX_NOACTIVATE   = 0x08000000

LWA_COLORKEY = 1
HWND_TOPMOST = -1
SWP_NOACTIVATE  = 0x0010
SWP_SHOWWINDOW  = 0x0040
SW_HIDE = 0
SW_SHOWNA = 8

WM_DESTROY    = 0x0002
WM_PAINT      = 0x000F
WM_TIMER      = 0x0113
WM_CLOSE      = 0x0010
WM_ERASEBKGND = 0x0014

PS_SOLID   = 0
NULL_BRUSH = 5

_TIMER_ID   = 1
_UPDATE_MS  = 500
_BORDER_PX  = 3

# 背景色 (用作透明 key, COLORREF = 0x00BBGGRR)
_KEY_COLOR = 0x00FF00FF           # 品红, 不会与绿边框冲突
# 边框色
_BORDER_COLOR = 0x0000FF00        # 绿色

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long,
    ctypes.wintypes.HWND,
    ctypes.c_uint,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize",        ctypes.c_uint),
        ("style",         ctypes.c_uint),
        ("lpfnWndProc",   WNDPROC),
        ("cbClsExtra",    ctypes.c_int),
        ("cbWndExtra",    ctypes.c_int),
        ("hInstance",      ctypes.wintypes.HINSTANCE),
        ("hIcon",         ctypes.wintypes.HICON),
        ("hCursor",       ctypes.wintypes.HANDLE),
        ("hbrBackground", ctypes.wintypes.HBRUSH),
        ("lpszMenuName",  ctypes.wintypes.LPCWSTR),
        ("lpszClassName", ctypes.wintypes.LPCWSTR),
        ("hIconSm",       ctypes.wintypes.HICON),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc",          ctypes.wintypes.HDC),
        ("fErase",       ctypes.wintypes.BOOL),
        ("rcPaint",      ctypes.wintypes.RECT),
        ("fRestore",     ctypes.wintypes.BOOL),
        ("fIncUpdate",   ctypes.wintypes.BOOL),
        ("rgbReserved",  ctypes.c_byte * 32),
    ]


class RoiOverlay:
    """透明 ROI 覆盖框"""

    _CLASS_NAME = "FishRoiOverlay"
    _class_atom = 0

    def __init__(self, window_manager):
        self._wm = window_manager
        self._hwnd = None
        self._thread = None
        self._running = False
        self._visible = False

    # ── 公开接口 ──

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._hwnd:
            try:
                user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass

    # ── Win32 窗口过程 ──

    def _wnd_proc_impl(self, hwnd, msg, wparam, lparam):
        if msg == WM_PAINT:
            ps = PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))

            rc = ctypes.wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rc))

            bg = gdi32.CreateSolidBrush(_KEY_COLOR)
            user32.FillRect(hdc, ctypes.byref(rc), bg)
            gdi32.DeleteObject(bg)

            pen = gdi32.CreatePen(PS_SOLID, _BORDER_PX, _BORDER_COLOR)
            old_pen = gdi32.SelectObject(hdc, pen)
            old_br = gdi32.SelectObject(hdc, gdi32.GetStockObject(NULL_BRUSH))
            gdi32.Rectangle(hdc, 0, 0, rc.right, rc.bottom)
            gdi32.SelectObject(hdc, old_pen)
            gdi32.SelectObject(hdc, old_br)
            gdi32.DeleteObject(pen)

            user32.EndPaint(hwnd, ctypes.byref(ps))
            return 0

        if msg == WM_ERASEBKGND:
            return 1

        if msg == WM_TIMER:
            self._update_position()
            return 0

        if msg == WM_CLOSE:
            user32.KillTimer(hwnd, _TIMER_ID)
            user32.DestroyWindow(hwnd)
            return 0

        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0

        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ── 线程主体 ──

    def _run(self):
        self._proc = WNDPROC(self._wnd_proc_impl)
        hinstance = kernel32.GetModuleHandleW(None)

        if RoiOverlay._class_atom == 0:
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = self._proc
            wc.hInstance = hinstance
            wc.lpszClassName = self._CLASS_NAME
            atom = user32.RegisterClassExW(ctypes.byref(wc))
            if atom:
                RoiOverlay._class_atom = atom

        ex = (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
              | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)

        self._hwnd = user32.CreateWindowExW(
            ex, self._CLASS_NAME, "ROI",
            WS_POPUP,
            0, 0, 1, 1,
            0, 0, hinstance, 0,
        )

        if not self._hwnd:
            log.warning_t("overlay.log.createFailed")
            return

        user32.SetLayeredWindowAttributes(self._hwnd, _KEY_COLOR, 0, LWA_COLORKEY)
        user32.SetTimer(self._hwnd, _TIMER_ID, _UPDATE_MS, 0)

        self._update_position()

        msg = ctypes.wintypes.MSG()
        while self._running:
            ret = user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
            if ret <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        self._hwnd = None
        self._visible = False

    # ── 位置同步 ──

    def _update_position(self):
        if not self._hwnd:
            return

        roi = config.DETECT_ROI
        if roi is None or not self._wm.is_valid():
            if self._visible:
                user32.ShowWindow(self._hwnd, SW_HIDE)
                self._visible = False
            return

        region = self._wm.get_region()
        if not region:
            if self._visible:
                user32.ShowWindow(self._hwnd, SW_HIDE)
                self._visible = False
            return

        win_x, win_y, win_w, win_h = region
        roi_x, roi_y, roi_w, roi_h = roi

        sx = win_x + roi_x
        sy = win_y + roi_y
        sw = min(roi_w, win_w - roi_x)
        sh = min(roi_h, win_h - roi_y)

        if sw <= 10 or sh <= 10:
            if self._visible:
                user32.ShowWindow(self._hwnd, SW_HIDE)
                self._visible = False
            return

        user32.SetWindowPos(
            self._hwnd, HWND_TOPMOST,
            sx, sy, sw, sh,
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

        if not self._visible:
            user32.ShowWindow(self._hwnd, SW_SHOWNA)
            self._visible = True

        user32.InvalidateRect(self._hwnd, 0, True)
