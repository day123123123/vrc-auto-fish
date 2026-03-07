"""
tkinter GUI 模块
================
主控制面板：状态显示、控制按钮、参数调节、实时日志输出。
Bot 在后台线程运行，GUI 通过共享属性 + 日志队列通信。
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import queue
import json
import keyboard
import cv2

import config
from core.bot import FishingBot
from utils.logger import log
from utils.i18n import t


# ═══════════════════════════════════════════════════════════
#  Tunable parameter definitions
#  (label, config attr, type, tooltip)  — labels/tips loaded from locale at runtime
#  type: "int" / "float" / "ms" (display ms, store seconds) / "pct"
# ═══════════════════════════════════════════════════════════
_TUNABLE_PARAM_KEYS = [
    ("BITE_FORCE_HOOK",   "float"),
    ("FISH_GAME_SIZE",    "int"),
    ("DEAD_ZONE",         "int"),
    ("HOLD_MIN_S",        "ms"),
    ("HOLD_MAX_S",        "ms"),
    ("HOLD_GAIN",         "float"),
    ("PREDICT_AHEAD",     "float"),
    ("SPEED_DAMPING",     "float"),
    ("MAX_FISH_BAR_DIST", "int"),
    ("VELOCITY_SMOOTH",   "float"),
    ("TRACK_MIN_ANGLE",   "float"),
    ("TRACK_MAX_ANGLE",   "float"),
    ("REGION_UP",         "int"),
    ("REGION_DOWN",       "int"),
    ("REGION_X",          "int"),
    ("POST_CATCH_DELAY",  "float"),
    ("INITIAL_PRESS_TIME","float"),
    ("VERIFY_CONSECUTIVE","int"),
    ("SUCCESS_PROGRESS",  "pct"),
]


def _get_tunable_params():
    """Return TUNABLE_PARAMS with localized labels and tooltips."""
    result = []
    for attr, vtype in _TUNABLE_PARAM_KEYS:
        pair = t(f"params.{attr}")   # [label, tooltip]
        result.append((pair[0], attr, vtype, pair[1]))
    return result


class FishingApp:
    """VRChat 自动钓鱼 — 主窗口"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("VRC auto fish 263302")
        self.root.geometry("580x800")
        self.root.resizable(True, True)
        self.root.minsize(520, 600)
        # ★ 默认不置顶 (用户可通过复选框开启)
        self.root.attributes("-topmost", False)

        # ── 机器人实例 ──
        self.bot = FishingBot()
        self.bot_thread: threading.Thread | None = None

        # ── 参数变量 ──
        self._param_vars = {}        # config属性名 → tk.StringVar

        # ── 构建界面 ──
        self._build_ui()

        # ── 加载上次保存的参数 ──
        self._load_settings()

        # ── 预加载 YOLO ──
        if self.bot.yolo is None:
            self._preload_yolo()

        # ── 注册全局快捷键 ──
        keyboard.add_hotkey(config.HOTKEY_TOGGLE, self._toggle_from_hotkey)
        keyboard.add_hotkey(config.HOTKEY_STOP,   self._stop_from_hotkey)
        keyboard.add_hotkey(config.HOTKEY_DEBUG,   self._toggle_debug_from_hotkey)

        # ── 启动轮询 ──
        self._poll()

        # ── 关闭处理 ──
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._log_msg("GitHub: https://github.com/day123123123/vrc-auto-fish")

    # ══════════════════════════════════════════════════════
    #  界面构建
    # ══════════════════════════════════════════════════════

    def _build_ui(self):
        pad = {"padx": 10, "pady": 5}

        # ── Status panel ──
        frm_status = ttk.LabelFrame(self.root, text=t("gui.status_frame"))
        frm_status.pack(fill="x", **pad)

        grid_pad = {"padx": 8, "pady": 3, "sticky": "w"}

        self.var_state = tk.StringVar(value=t("state.ready"))
        self.var_window = tk.StringVar(value=t("gui.not_connected"))
        self.var_count = tk.StringVar(value="0")
        self.var_debug = tk.StringVar(value=t("gui.debug_off"))

        ttk.Label(frm_status, text=t("gui.state_label")).grid(row=0, column=0, **grid_pad)
        self.lbl_state = ttk.Label(frm_status, textvariable=self.var_state,
                                   foreground="gray")
        self.lbl_state.grid(row=0, column=1, **grid_pad)

        ttk.Label(frm_status, text=t("gui.window_label")).grid(row=1, column=0, **grid_pad)
        self.lbl_window = ttk.Label(frm_status, textvariable=self.var_window)
        self.lbl_window.grid(row=1, column=1, **grid_pad)

        ttk.Label(frm_status, text=t("gui.count_label")).grid(row=2, column=0, **grid_pad)
        ttk.Label(frm_status, textvariable=self.var_count).grid(
            row=2, column=1, **grid_pad)

        ttk.Label(frm_status, text=t("gui.debug_label")).grid(row=3, column=0, **grid_pad)
        ttk.Label(frm_status, textvariable=self.var_debug).grid(
            row=3, column=1, **grid_pad)

        # ── 中间：控制按钮 ──
        frm_ctrl = ttk.Frame(self.root)
        frm_ctrl.pack(fill="x", **pad)

        self.btn_start = ttk.Button(frm_ctrl, text=t("gui.btn_start"),
                                    command=self._on_start, width=15)
        self.btn_start.pack(side="left", padx=5)

        self.btn_stop = ttk.Button(frm_ctrl, text=t("gui.btn_stop"),
                                   command=self._on_stop, width=15,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=5)

        self.btn_debug = ttk.Button(frm_ctrl, text=t("gui.btn_debug"),
                                    command=self._on_toggle_debug, width=15)
        self.btn_debug.pack(side="left", padx=5)

        # ── 辅助按钮 ──
        frm_aux = ttk.Frame(self.root)
        frm_aux.pack(fill="x", **pad)

        self.btn_connect = ttk.Button(frm_aux, text=t("gui.btn_connect"),
                                      command=self._on_connect, width=15)
        self.btn_connect.pack(side="left", padx=5)

        self.btn_screenshot = ttk.Button(frm_aux, text=t("gui.btn_screenshot"),
                                         command=self._on_screenshot, width=15)
        self.btn_screenshot.pack(side="left", padx=5)

        self.btn_clearlog = ttk.Button(frm_aux, text=t("gui.btn_clearlog"),
                                       command=self._on_clear_log, width=12)
        self.btn_clearlog.pack(side="left", padx=5)

        self.btn_whitelist = ttk.Button(frm_aux, text=t("gui.btn_whitelist"),
                                        command=self._on_whitelist, width=12)
        self.btn_whitelist.pack(side="left", padx=5)

        # ── 开关选项（独立一行，防窗口太窄时被挤掉） ──
        frm_toggles = ttk.Frame(self.root)
        frm_toggles.pack(fill="x", **pad)

        self.var_topmost = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm_toggles, text=t("gui.chk_topmost"),
                        variable=self.var_topmost,
                        command=self._on_topmost).pack(side="left", padx=5)

        self.var_show_debug = tk.BooleanVar(value=config.SHOW_DEBUG)
        ttk.Checkbutton(frm_toggles, text=t("gui.chk_debug_win"),
                        variable=self.var_show_debug,
                        command=self._on_debug_toggle).pack(side="left", padx=5)

        self.var_skip_success = tk.BooleanVar(value=getattr(config, "SKIP_SUCCESS_CHECK", False))
        chk_skip = ttk.Checkbutton(frm_toggles, text=t("gui.chk_skip_success"),
                                   variable=self.var_skip_success,
                                   command=self._on_skip_success_toggle)
        chk_skip.pack(side="left", padx=5)
        self._create_tooltip(chk_skip, t("gui.tip_skip_success"))

        # ── Anti-jam ──
        frm_anti = ttk.LabelFrame(self.root, text=t("gui.anti_jam_frame"))
        frm_anti.pack(fill="x", **pad)

        row_mode = ttk.Frame(frm_anti)
        row_mode.pack(fill="x", padx=5, pady=2)

        self.var_anti_mode = tk.StringVar(
            value=getattr(config, "ANTI_STUCK_MODE", "shake"))
        rb_shake = ttk.Radiobutton(row_mode, text=t("gui.rb_shake"),
                                   variable=self.var_anti_mode, value="shake",
                                   command=self._on_anti_mode_change)
        rb_shake.pack(side="left", padx=5)
        self._create_tooltip(rb_shake, t("gui.tip_shake"))

        rb_jump = ttk.Radiobutton(row_mode, text=t("gui.rb_jump"),
                                   variable=self.var_anti_mode, value="jump",
                                   command=self._on_anti_mode_change)
        rb_jump.pack(side="left", padx=5)
        self._create_tooltip(rb_jump, t("gui.tip_jump"))

        row_params = ttk.Frame(frm_anti)
        row_params.pack(fill="x", padx=5, pady=2)

        ttk.Label(row_params, text=t("gui.shake_time_label")).pack(side="left", padx=(5, 2))
        self.var_shake_time = tk.StringVar(
            value=f"{config.SHAKE_HEAD_TIME:.3f}")
        ent_shake = ttk.Entry(row_params, textvariable=self.var_shake_time,
                              width=6, justify="center")
        ent_shake.pack(side="left", padx=2)
        ent_shake.bind("<Return>", lambda e: self._apply_anti_params())
        ent_shake.bind("<FocusOut>", lambda e: self._apply_anti_params())
        self._create_tooltip(ent_shake, t("gui.tip_shake_time"))

        # ── YOLO detection ──
        frm_yolo = ttk.LabelFrame(self.root, text=t("gui.yolo_frame"))
        frm_yolo.pack(fill="x", **pad)

        config.USE_YOLO = True
        ttk.Label(frm_yolo, text="YOLO ✓").pack(side="left", padx=5)

        self.var_yolo_collect = tk.BooleanVar(value=config.YOLO_COLLECT)
        ttk.Checkbutton(frm_yolo, text=t("gui.collect_data"),
                        variable=self.var_yolo_collect,
                        command=self._on_yolo_collect_toggle).pack(
                            side="left", padx=5)

        ttk.Label(frm_yolo, text=t("gui.device_label")).pack(side="left", padx=(10, 2))
        self.var_yolo_device = tk.StringVar(value=config.YOLO_DEVICE)
        cmb_dev = ttk.Combobox(frm_yolo, textvariable=self.var_yolo_device,
                               values=["auto", "cpu", "gpu"],
                               state="readonly", width=5)
        cmb_dev.pack(side="left", padx=2)
        cmb_dev.bind("<<ComboboxSelected>>", self._on_yolo_device_change)

        self.var_yolo_status = tk.StringVar(value="")
        self._update_yolo_status()
        ttk.Label(frm_yolo, textvariable=self.var_yolo_status,
                  foreground="gray").pack(side="left", padx=10)

        # ── 检测区域框选 ──
        frm_roi = ttk.Frame(self.root)
        frm_roi.pack(fill="x", **pad)

        self.btn_roi = ttk.Button(frm_roi, text=t("gui.btn_roi"),
                                  command=self._on_select_roi, width=15)
        self.btn_roi.pack(side="left", padx=5)

        self.btn_clear_roi = ttk.Button(frm_roi, text=t("gui.btn_clear_roi"),
                                        command=self._on_clear_roi, width=12)
        self.btn_clear_roi.pack(side="left", padx=5)

        self.var_roi = tk.StringVar(value=t("gui.roi_not_set"))
        ttk.Label(frm_roi, text=t("gui.detect_region_label")).pack(side="left", padx=(10, 2))
        self.lbl_roi = ttk.Label(frm_roi, textvariable=self.var_roi,
                                 foreground="gray")
        self.lbl_roi.pack(side="left")

        # (行为克隆 UI 已移除)

        # ── 参数调节面板 ──
        self._build_params_panel(pad)

        # ── Log panel ──
        frm_log = ttk.LabelFrame(self.root, text=t("gui.log_frame"))
        frm_log.pack(fill="both", expand=True, **pad)

        self.txt_log = scrolledtext.ScrolledText(
            frm_log, height=14, state="disabled",
            font=("Consolas", 9), wrap="word",
            bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="#d4d4d4",
        )
        self.txt_log.pack(fill="both", expand=True, padx=5, pady=5)

    # ══════════════════════════════════════════════════════
    #  参数调节面板
    # ══════════════════════════════════════════════════════

    def _build_params_panel(self, pad):
        """Build the mini-game parameter tuning panel."""
        frm = ttk.LabelFrame(self.root, text=t("gui.params_frame"))
        frm.pack(fill="x", **pad)

        # 4-column layout: [label entry] [label entry]
        cols_per_row = 2
        gpad = {"padx": 4, "pady": 2}

        TUNABLE_PARAMS = _get_tunable_params()
        for i, (label, attr, vtype, tip) in enumerate(TUNABLE_PARAMS):
            row = i // cols_per_row
            col_base = (i % cols_per_row) * 3   # 每组占3列: label, entry, unit

            # 从 config 读取当前值并转换为显示值
            display_val = self._config_to_display(attr, vtype)
            var = tk.StringVar(value=display_val)
            self._param_vars[attr] = (var, vtype)

            # 标签
            lbl = ttk.Label(frm, text=label, width=12, anchor="e")
            lbl.grid(row=row, column=col_base, sticky="e", **gpad)

            # 输入框
            entry = ttk.Entry(frm, textvariable=var, width=8,
                              justify="center")
            entry.grid(row=row, column=col_base + 1, sticky="w", **gpad)

            # 绑定回车和失焦自动应用
            entry.bind("<Return>", lambda e: self._apply_params())
            entry.bind("<FocusOut>", lambda e: self._apply_params())

            # 提示 (鼠标悬停)
            if tip:
                self._create_tooltip(entry, tip)

        # 按钮行
        total_rows = (len(TUNABLE_PARAMS) + cols_per_row - 1) // cols_per_row
        btn_frame = ttk.Frame(frm)
        btn_frame.grid(row=total_rows, column=0, columnspan=6,
                       pady=(5, 5), sticky="e", padx=10)

        ttk.Button(btn_frame, text=t("gui.apply_params"),
                   command=self._apply_params, width=10).pack(side="left", padx=3)
        ttk.Button(btn_frame, text=t("gui.reset_params"),
                   command=self._reset_params, width=10).pack(side="left", padx=3)

    def _config_to_display(self, attr: str, vtype: str) -> str:
        """将 config 值转换为 GUI 显示值"""
        val = getattr(config, attr)
        if vtype == "ms":
            return str(round(val * 1000))       # 秒 → 毫秒
        elif vtype == "pct":
            return str(round(val * 100))        # 0.55 → 55
        elif vtype == "int":
            return str(int(val))
        elif vtype == "float":
            # 自动选择合理的小数位
            if val == 0:
                return "0"
            elif abs(val) < 0.001:
                return f"{val:.5f}"
            elif abs(val) < 0.1:
                return f"{val:.4f}"
            elif abs(val) < 10:
                return f"{val:.3f}"
            else:
                return f"{val:.1f}"
        return str(val)

    def _display_to_config(self, text: str, vtype: str):
        """将 GUI 显示值转换为 config 值"""
        text = text.strip()
        if not text:
            return None
        try:
            if vtype == "ms":
                return float(text) / 1000.0     # 毫秒 → 秒
            elif vtype == "pct":
                return float(text) / 100.0      # 55 → 0.55
            elif vtype == "int":
                return int(float(text))
            elif vtype == "float":
                return float(text)
        except ValueError:
            return None
        return None

    def _apply_params(self):
        """读取所有参数输入框，应用到 config 并保存到文件"""
        changed = []
        for attr, (var, vtype) in self._param_vars.items():
            new_val = self._display_to_config(var.get(), vtype)
            if new_val is None:
                continue

            old_val = getattr(config, attr)
            if vtype == "ms":
                is_same = abs(old_val - new_val) < 0.0001
            elif vtype == "float":
                is_same = abs(old_val - new_val) < 1e-7
            else:
                is_same = old_val == new_val

            if not is_same:
                setattr(config, attr, new_val)
                changed.append(f"{attr}: {old_val} → {new_val}")

        if changed:
            self._save_settings()
            self._log_msg(t("gui.log_params_updated", changes=", ".join(changed)))

    def _reset_params(self):
        """恢复所有参数到默认值并删除配置文件"""
        defaults = {
            "BITE_FORCE_HOOK":  18.0,
            "FISH_GAME_SIZE":   20,
            "DEAD_ZONE":        15,
            "HOLD_MIN_S":       0.025,
            "HOLD_MAX_S":       0.100,
            "HOLD_GAIN":        0.040,
            "PREDICT_AHEAD":    0.5,
            "SPEED_DAMPING":    0.00025,
            "MAX_FISH_BAR_DIST": 300,
            "VELOCITY_SMOOTH":  0.5,
            "TRACK_MIN_ANGLE":  3.0,
            "TRACK_MAX_ANGLE":  45.0,
            "REGION_UP":        300,
            "REGION_DOWN":      400,
            "REGION_X":         100,
            "POST_CATCH_DELAY": 3.0,
            "INITIAL_PRESS_TIME": 0.2,
            "VERIFY_CONSECUTIVE": 1,
            "SUCCESS_PROGRESS": 0.55,
        }

        for attr, default_val in defaults.items():
            setattr(config, attr, default_val)
            if attr in self._param_vars:
                var, vtype = self._param_vars[attr]
                var.set(self._config_to_display(attr, vtype))

        config.SKIP_SUCCESS_CHECK = False
        if hasattr(self, 'var_skip_success'):
            self.var_skip_success.set(False)
        config.ANTI_STUCK_MODE = "shake"
        if hasattr(self, 'var_anti_mode'):
            self.var_anti_mode.set("shake")
        config.SHAKE_HEAD_TIME = 0.02
        if hasattr(self, 'var_shake_time'):
            self.var_shake_time.set("0.020")

        # 删除配置文件
        try:
            import os
            if os.path.exists(config.SETTINGS_FILE):
                os.remove(config.SETTINGS_FILE)
        except Exception:
            pass
        self._log_msg(t("gui.log_params_reset"))

    # ══════════════════════════════════════════════════════
    #  参数持久化
    # ══════════════════════════════════════════════════════

    def _save_settings(self):
        """将当前可调参数保存到 settings.json"""
        data = {}
        for attr, (_, vtype) in self._param_vars.items():
            data[attr] = getattr(config, attr)
        data["DETECT_ROI"] = config.DETECT_ROI
        data["YOLO_COLLECT"] = config.YOLO_COLLECT
        data["YOLO_DEVICE"] = config.YOLO_DEVICE
        data["SHOW_DEBUG"] = config.SHOW_DEBUG
        data["FISH_WHITELIST"] = config.FISH_WHITELIST
        data["SKIP_SUCCESS_CHECK"] = config.SKIP_SUCCESS_CHECK
        data["ANTI_STUCK_MODE"] = config.ANTI_STUCK_MODE
        data["SHAKE_HEAD_TIME"] = config.SHAKE_HEAD_TIME
        try:
            with open(config.SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self._log_msg(t("gui.log_save_config_fail", e=e))

    def _load_settings(self):
        """启动时从 settings.json 加载参数"""
        import os
        if not os.path.exists(config.SETTINGS_FILE):
            return
        try:
            with open(config.SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("HOLD_GAIN", 1) < 0.02:
                data["HOLD_GAIN"] = 0.040
            if data.get("SPEED_DAMPING", 0) > 0.001:
                data["SPEED_DAMPING"] = 0.00025
            if data.get("HOLD_MAX_S", 1) < 0.08:
                data["HOLD_MAX_S"] = 0.100
            if data.get("HOLD_MIN_S", 1) < 0.02:
                data["HOLD_MIN_S"] = 0.025

            loaded = []
            for attr, val in data.items():
                if attr == "DETECT_ROI":
                    if val and isinstance(val, list) and len(val) == 4:
                        config.DETECT_ROI = val
                        if hasattr(self, 'var_roi'):
                            x, y, w, h = val
                            self.var_roi.set(f"X={x} Y={y} {w}x{h}")
                            self.lbl_roi.config(foreground="green")
                    else:
                        config.DETECT_ROI = None
                    loaded.append(attr)
                elif attr == "YOLO_COLLECT":
                    config.YOLO_COLLECT = bool(val)
                    if hasattr(self, 'var_yolo_collect'):
                        self.var_yolo_collect.set(config.YOLO_COLLECT)
                    loaded.append(attr)
                elif attr == "YOLO_DEVICE":
                    if val in ("auto", "cpu", "gpu"):
                        config.YOLO_DEVICE = val
                        if hasattr(self, 'var_yolo_device'):
                            self.var_yolo_device.set(val)
                    loaded.append(attr)
                elif attr == "SHOW_DEBUG":
                    config.SHOW_DEBUG = bool(val)
                    if hasattr(self, 'var_show_debug'):
                        self.var_show_debug.set(config.SHOW_DEBUG)
                    loaded.append(attr)
                elif attr == "FISH_WHITELIST":
                    if isinstance(val, dict):
                        config.FISH_WHITELIST.update(val)
                    loaded.append(attr)
                elif attr == "SKIP_SUCCESS_CHECK":
                    config.SKIP_SUCCESS_CHECK = bool(val)
                    if hasattr(self, 'var_skip_success'):
                        self.var_skip_success.set(config.SKIP_SUCCESS_CHECK)
                    loaded.append(attr)
                elif attr == "ANTI_STUCK_MODE":
                    if val == "crouch":
                        val = "jump"
                    if val in ("shake", "jump"):
                        config.ANTI_STUCK_MODE = val
                        if hasattr(self, 'var_anti_mode'):
                            self.var_anti_mode.set(val)
                    loaded.append(attr)
                elif attr == "SHAKE_HEAD_TIME":
                    config.SHAKE_HEAD_TIME = float(val)
                    if hasattr(self, 'var_shake_time'):
                        self.var_shake_time.set(f"{config.SHAKE_HEAD_TIME:.3f}")
                    loaded.append(attr)
                elif attr in self._param_vars:
                    setattr(config, attr, val)
                    var, vtype = self._param_vars[attr]
                    var.set(self._config_to_display(attr, vtype))
                    loaded.append(attr)
            if loaded:
                pass
        except Exception as e:
            self._log_msg(t("gui.log_load_config_fail", e=e))

    @staticmethod
    def _create_tooltip(widget, text: str):
        """为控件创建鼠标悬停提示"""
        tip_window = [None]

        def show(event):
            if tip_window[0]:
                return
            tw = tk.Toplevel(widget)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{event.x_root+10}+{event.y_root+10}")
            lbl = tk.Label(tw, text=text, background="#ffffe0",
                           relief="solid", borderwidth=1,
                           font=("", 9), padx=4, pady=2)
            lbl.pack()
            tip_window[0] = tw

        def hide(_):
            if tip_window[0]:
                tip_window[0].destroy()
                tip_window[0] = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    # ══════════════════════════════════════════════════════
    #  按钮回调
    # ══════════════════════════════════════════════════════

    @staticmethod
    def _has_non_ascii(path: str) -> bool:
        try:
            path.encode("ascii")
            return False
        except UnicodeEncodeError:
            return True

    def _on_start(self):
        """开始钓鱼"""
        if self.bot.running:
            return

        if self._has_non_ascii(config.BASE_DIR):
            self._log_msg(t("gui.log_path_error"))
            self._log_msg(t("gui.log_path_current", path=config.BASE_DIR))
            self._log_msg(t("gui.log_path_hint"))
            return

        # Try to connect window first
        if not self.bot.window.is_valid():
            if not self.bot.window.find():
                self._log_msg(t("gui.log_not_found"))
                return

        self.var_window.set(f"{self.bot.window.title} (HWND={self.bot.window.hwnd})")

        # ★ 开始前应用一次参数 (确保 GUI 上的值生效)
        self._apply_params()

        self.bot.running = True
        self.bot.state = t("state.running")

        # Start background thread
        if self.bot_thread is None or not self.bot_thread.is_alive():
            self.bot_thread = threading.Thread(target=self.bot.run, daemon=True)
            self.bot_thread.start()

        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.btn_roi.config(state="disabled")
        self.btn_clear_roi.config(state="disabled")
        self._log_msg(t("gui.log_start"))

    def _on_stop(self):
        """停止钓鱼"""
        self.bot.running = False
        self.bot.input.safe_release()
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.btn_roi.config(state="normal")
        self.btn_clear_roi.config(state="normal")
        self._log_msg(t("gui.log_stop"))
        self._save_log()

    def _on_toggle_debug(self):
        """Toggle debug mode."""
        self.bot.debug_mode = not self.bot.debug_mode
        if self.bot.debug_mode:
            self.var_debug.set(t("gui.debug_on"))
            self._log_msg(t("gui.log_debug_on"))
            self._log_msg(t("gui.log_debug_hint"))
        else:
            self.var_debug.set(t("gui.debug_off"))
            self._log_msg(t("gui.log_debug_off"))

    def _on_connect(self):
        """手动连接 VRChat 窗口"""
        if self.bot.window.find():
            self.var_window.set(
                f"{self.bot.window.title} (HWND={self.bot.window.hwnd})"
            )
            # Reset capture method; will re-detect PrintWindow on next capture
            self.bot.screen.reset_capture_method()
            self._log_msg(t("gui.log_connected", title=self.bot.window.title))
        else:
            self.var_window.set(t("gui.not_connected"))
            self._log_msg(t("gui.log_not_found"))

    def _on_screenshot(self):
        """手动保存当前截图（调试用）"""
        if not self.bot.window.is_valid():
            if not self.bot.window.find():
                self._log_msg(t("gui.log_screenshot_no_window"))
                return

        img, region = self.screen_capture_safe()
        if img is not None:
            self.bot.screen.save_debug(img, "manual_screenshot")
            h, w = img.shape[:2]
            self._log_msg(t("gui.log_screenshot_saved", w=w, h=h))
            if region:
                self._log_msg(t("gui.log_screenshot_region",
                    x=region[0], y=region[1], w=region[2], h=region[3]))
        else:
            self._log_msg(t("gui.log_screenshot_fail"))

    def _on_clear_log(self):
        """清空日志文本框"""
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.config(state="disabled")

    def _on_whitelist(self):
        """Popup: select which fish types to catch."""
        FISH_KEYS = [
            "fish_black", "fish_white", "fish_copper", "fish_green",
            "fish_blue", "fish_purple", "fish_pink", "fish_red", "fish_rainbow",
        ]
        win = tk.Toplevel(self.root)
        win.title(t("gui.whitelist_title"))
        win.geometry("200x320")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        ttk.Label(win, text=t("gui.whitelist_label")).pack(pady=(10, 5))

        wl = config.FISH_WHITELIST
        chk_vars = {}
        for key in FISH_KEYS:
            var = tk.BooleanVar(value=wl.get(key, True))
            chk_vars[key] = var
            ttk.Checkbutton(win, text=t(f"fish.{key}"), variable=var).pack(
                anchor="w", padx=30)

        def _apply():
            for key, var in chk_vars.items():
                config.FISH_WHITELIST[key] = var.get()
            self._save_settings()
            enabled = [t(f"fish.{k}") for k in FISH_KEYS if chk_vars[k].get()]
            self._log_msg(t("gui.log_whitelist_updated", names=", ".join(enabled)))
            win.destroy()

        ttk.Button(win, text=t("gui.whitelist_ok"), command=_apply).pack(pady=10)

    def _on_topmost(self):
        """切换窗口置顶 (用 int 0/1 确保兼容性)"""
        topmost = self.var_topmost.get()
        self.root.wm_attributes("-topmost", 1 if topmost else 0)
        if not topmost:
            self.root.lift()
            self.root.focus_force()

    def _on_debug_toggle(self):
        """Toggle debug window display."""
        config.SHOW_DEBUG = self.var_show_debug.get()
        self._save_settings()
        if config.SHOW_DEBUG:
            self._log_msg(t("gui.log_debug_win_on"))
        else:
            self._log_msg(t("gui.log_debug_win_off"))
            try:
                import cv2
                cv2.destroyWindow("Debug Overlay")
            except Exception:
                pass

    def _on_skip_success_toggle(self):
        """Toggle skip-success-check setting."""
        config.SKIP_SUCCESS_CHECK = self.var_skip_success.get()
        self._save_settings()
        if config.SKIP_SUCCESS_CHECK:
            self._log_msg(t("gui.log_skip_on"))
        else:
            self._log_msg(t("gui.log_skip_off"))

    def _on_anti_mode_change(self):
        """Switch anti-jam mode."""
        mode = self.var_anti_mode.get()
        config.ANTI_STUCK_MODE = mode
        self._save_settings()
        label_key = "log_anti_shake" if mode == "shake" else "log_anti_jump"
        self._log_msg(t("gui.log_anti_mode", mode=t(f"gui.{label_key}")))

    def _apply_anti_params(self):
        """Apply anti-jam parameters (shake duration)."""
        changed = []
        try:
            val = float(self.var_shake_time.get().strip())
            if abs(val - config.SHAKE_HEAD_TIME) > 1e-6:
                config.SHAKE_HEAD_TIME = val
                changed.append(t("gui.log_shake_time", val=val))
        except ValueError:
            pass

        if changed:
            self._save_settings()
            self._log_msg(t("gui.log_anti_params", changes=", ".join(changed)))

    def _preload_yolo(self):
        """Preload YOLO model in a background thread to avoid blocking the GUI."""
        def _load():
            try:
                from core.bot import _get_yolo_detector
                self.bot.yolo = _get_yolo_detector()
            except Exception as e:
                self._log_msg(t("gui.log_yolo_preload_fail", e=e))
        _thread = threading.Thread(target=_load, daemon=True)
        _thread.start()

    def _on_yolo_collect_toggle(self):
        """Toggle YOLO data collection mode."""
        collect = self.var_yolo_collect.get()
        config.YOLO_COLLECT = collect
        self._save_settings()
        if collect:
            self._log_msg(t("gui.log_yolo_collect_on"))
        else:
            self._log_msg(t("gui.log_yolo_collect_off"))

    def _on_yolo_device_change(self, _event=None):
        """Switch YOLO inference device."""
        dev = self.var_yolo_device.get()
        config.YOLO_DEVICE = dev
        self._save_settings()
        dev_key = {"auto": "log_yolo_device_auto",
                   "cpu":  "log_yolo_device_cpu",
                   "gpu":  "log_yolo_device_gpu"}.get(dev, dev)
        label = t(f"gui.{dev_key}") if dev in ("auto", "cpu", "gpu") else dev
        self._log_msg(t("gui.log_yolo_device", label=label))

    def _update_yolo_status(self):
        """Update YOLO status display."""
        import os as _os
        model_ok = _os.path.exists(config.YOLO_MODEL)
        unlabeled = _os.path.join(
            config.BASE_DIR, "yolo", "dataset", "images", "unlabeled")
        train = _os.path.join(
            config.BASE_DIR, "yolo", "dataset", "images", "train")
        n_unlabeled = len([
            f for f in _os.listdir(unlabeled)
            if f.endswith((".png", ".jpg"))
        ]) if _os.path.isdir(unlabeled) else 0
        n_train = len([
            f for f in _os.listdir(train)
            if f.endswith((".png", ".jpg"))
        ]) if _os.path.isdir(train) else 0

        parts = []
        parts.append(t("gui.yolo_model_ok") if model_ok else t("gui.yolo_model_missing"))
        parts.append(t("gui.yolo_train_count", n=n_train))
        parts.append(t("gui.yolo_unlabeled_count", n=n_unlabeled))
        self.var_yolo_status.set(" | ".join(parts))

    def _on_select_roi(self):
        """框选钓鱼UI检测区域"""
        if not self.bot.window.is_valid():
            if not self.bot.window.find():
                self._log_msg(t("gui.log_roi_error_window"))
                return

        img, _ = self.screen_capture_safe()
        if img is None:
            self._log_msg(t("gui.log_roi_error_capture"))
            return

        self._log_msg(t("gui.log_roi_prompt"))

        win_name = "Select Fishing ROI - Enter=OK / Esc=Cancel"
        cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
        h, w = img.shape[:2]
        dw = min(w, 1280)
        dh = int(h * dw / w)
        cv2.resizeWindow(win_name, dw, dh)

        roi = cv2.selectROI(win_name, img,
                            fromCenter=False, showCrosshair=True)
        cv2.destroyAllWindows()

        x, y, w_r, h_r = [int(v) for v in roi]
        if w_r > 10 and h_r > 10:
            config.DETECT_ROI = [x, y, w_r, h_r]
            self._save_settings()
            self.var_roi.set(f"X={x} Y={y} {w_r}x{h_r}")
            self.lbl_roi.config(foreground="green")
            self._log_msg(t("gui.log_roi_set", x=x, y=y, w=w_r, h=h_r))
        else:
            self._log_msg(t("gui.log_roi_cancel"))

    def _on_clear_roi(self):
        """Clear the ROI selection."""
        config.DETECT_ROI = None
        self._save_settings()
        self.var_roi.set(t("gui.roi_not_set"))
        self.lbl_roi.config(foreground="gray")
        self._log_msg(t("gui.log_roi_cleared"))

    def screen_capture_safe(self):
        """Safely capture the VRChat window."""
        try:
            return self.bot.screen.grab_window(self.bot.window)
        except Exception as e:
            self._log_msg(f"[Error] Screenshot error: {e}")
            return None, None

    # ══════════════════════════════════════════════════════
    #  快捷键回调（从 VRChat 中触发）
    # ══════════════════════════════════════════════════════

    def _toggle_from_hotkey(self):
        """F9 — 切换开始/停止"""
        if self.bot.running:
            self.root.after(0, self._on_stop)
        else:
            self.root.after(0, self._on_start)

    def _stop_from_hotkey(self):
        """F10 — 停止"""
        self.root.after(0, self._on_stop)

    def _toggle_debug_from_hotkey(self):
        """F11 — 调试"""
        self.root.after(0, self._on_toggle_debug)

    # ══════════════════════════════════════════════════════
    #  轮询更新
    # ══════════════════════════════════════════════════════

    def _poll(self):
        """每 100ms 从日志队列读取消息，更新状态面板"""
        # 读取日志
        try:
            for _ in range(20):          # 每次最多处理 20 条
                msg = log.log_queue.get_nowait()
                self._append_log(msg)
        except queue.Empty:
            pass

        # 更新状态
        self.var_state.set(self.bot.state)
        self.var_count.set(str(self.bot.fish_count))

        # 状态颜色
        if self.bot.running:
            self.lbl_state.config(foreground="green")
        else:
            self.lbl_state.config(foreground="gray")

        # 检测线程是否意外退出
        if self.bot_thread and not self.bot_thread.is_alive() and self.bot.running:
            self.bot.running = False
            self.btn_start.config(state="normal")
            self.btn_stop.config(state="disabled")
            self.btn_roi.config(state="normal")
            self.btn_clear_roi.config(state="normal")

        self.root.after(100, self._poll)

    # ══════════════════════════════════════════════════════
    #  日志输出
    # ══════════════════════════════════════════════════════

    def _log_msg(self, msg: str):
        """直接向日志区写入（不经过 logger queue）"""
        import time
        ts = time.strftime("%H:%M:%S")
        self._append_log(f"[{ts}] {msg}")

    def _append_log(self, text: str):
        """向日志文本框追加一行"""
        self.txt_log.config(state="normal")
        self.txt_log.insert("end", text + "\n")
        self.txt_log.see("end")
        self.txt_log.config(state="disabled")

    # ══════════════════════════════════════════════════════
    #  关闭
    # ══════════════════════════════════════════════════════

    def _on_close(self):
        """窗口关闭处理"""
        self.bot.running = False
        self.bot.input.safe_release()
        self._save_settings()
        self._save_log()
        self.root.destroy()

    def _save_log(self):
        """保存日志到文件 (覆盖上一次)"""
        import os
        path = os.path.join(config.DEBUG_DIR, "last_run.log")
        log.save(path)
        self._log_msg(t("gui.log_saved", path=path))
