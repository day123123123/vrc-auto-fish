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


# ═══════════════════════════════════════════════════════════
#  可调参数定义
#  (显示名, config属性名, 类型, 单位提示)
#  类型: "int" / "float" / "ms" (毫秒显示,秒存储)
# ═══════════════════════════════════════════════════════════
TUNABLE_PARAMS = [
    ("强制提竿(s)",   "BITE_FORCE_HOOK",  "float", "等待N秒无咬钩则强制提竿进入小游戏"),
    ("鱼像素大小",    "FISH_GAME_SIZE",   "int",   "游戏内鱼图标的大致像素,越小搜索倍率越高"),
    ("死区(px)",      "DEAD_ZONE",        "int",   "越大越容易触发按住"),
    ("抗重力基准(ms)","HOLD_MIN_S",       "ms",    "越小下降越快,越大越悬浮"),
    ("最长按住(ms)",  "HOLD_MAX_S",       "ms",    "单次按住的最大时长"),
    ("按住增益",      "HOLD_GAIN",        "float", "位置误差×增益=额外按住时长"),
    ("前瞻时间(s)",   "PREDICT_AHEAD",    "float", "预测未来位置的时间"),
    ("速度阻尼",      "SPEED_DAMPING",    "float", "下坠快加按住,上升快减按住"),
    ("最大距离(px)",  "MAX_FISH_BAR_DIST","int",   "鱼条距离超过视为误检"),
    ("速度平滑",      "VELOCITY_SMOOTH",  "float", "0~1, 越大越平滑"),
    ("旋转阈值(°)",   "TRACK_MIN_ANGLE",  "float", "轨道倾斜超过此角度启用旋转"),
    ("旋转上限(°)",   "TRACK_MAX_ANGLE",  "float", "超过此角度视为误检(如海平线)"),
    ("搜索上(px)",    "REGION_UP",        "int",   "白条锁定后向上搜索的像素数"),
    ("搜索下(px)",    "REGION_DOWN",      "int",   "白条锁定后向下搜索的像素数"),
    ("搜索X(px)",     "REGION_X",         "int",   "白条中心左右各N像素范围内检测"),
    ("归正时间(s)",   "POST_CATCH_DELAY", "float", "钓鱼结束/失败后等待N秒再抛竿"),
    ("摇头时长(s)",   "SHAKE_HEAD_TIME",  "float", "摇头每段按住时长,0=不摇头"),
    ("按压时间(s)",   "INITIAL_PRESS_TIME","float", "开局按压时长(开局延迟0.5s固定)"),
    ("确认帧数",      "VERIFY_CONSECUTIVE","int",   "连续几帧检测到UI才确认小游戏开始"),
]


class FishingApp:
    """VRChat 自动钓鱼 — 主窗口"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("VRC auto fish 263201")
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

        # ── 顶部：状态面板 ──
        frm_status = ttk.LabelFrame(self.root, text=" 状态 ")
        frm_status.pack(fill="x", **pad)

        grid_pad = {"padx": 8, "pady": 3, "sticky": "w"}

        self.var_state = tk.StringVar(value="就绪")
        self.var_window = tk.StringVar(value="未连接")
        self.var_count = tk.StringVar(value="0")
        self.var_debug = tk.StringVar(value="关闭")

        ttk.Label(frm_status, text="运行状态:").grid(row=0, column=0, **grid_pad)
        self.lbl_state = ttk.Label(frm_status, textvariable=self.var_state,
                                   foreground="gray")
        self.lbl_state.grid(row=0, column=1, **grid_pad)

        ttk.Label(frm_status, text="VRChat窗口:").grid(row=1, column=0, **grid_pad)
        self.lbl_window = ttk.Label(frm_status, textvariable=self.var_window)
        self.lbl_window.grid(row=1, column=1, **grid_pad)

        ttk.Label(frm_status, text="钓鱼次数:").grid(row=2, column=0, **grid_pad)
        ttk.Label(frm_status, textvariable=self.var_count).grid(
            row=2, column=1, **grid_pad)

        ttk.Label(frm_status, text="调试模式:").grid(row=3, column=0, **grid_pad)
        ttk.Label(frm_status, textvariable=self.var_debug).grid(
            row=3, column=1, **grid_pad)

        # ── 中间：控制按钮 ──
        frm_ctrl = ttk.Frame(self.root)
        frm_ctrl.pack(fill="x", **pad)

        self.btn_start = ttk.Button(frm_ctrl, text="▶ 开始 (F9)",
                                    command=self._on_start, width=15)
        self.btn_start.pack(side="left", padx=5)

        self.btn_stop = ttk.Button(frm_ctrl, text="■ 停止 (F10)",
                                   command=self._on_stop, width=15,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=5)

        self.btn_debug = ttk.Button(frm_ctrl, text="调试模式 (F11)",
                                    command=self._on_toggle_debug, width=15)
        self.btn_debug.pack(side="left", padx=5)

        # ── 辅助按钮 ──
        frm_aux = ttk.Frame(self.root)
        frm_aux.pack(fill="x", **pad)

        self.btn_connect = ttk.Button(frm_aux, text="🔗 连接窗口",
                                      command=self._on_connect, width=15)
        self.btn_connect.pack(side="left", padx=5)

        self.btn_screenshot = ttk.Button(frm_aux, text="📸 保存截图",
                                         command=self._on_screenshot, width=15)
        self.btn_screenshot.pack(side="left", padx=5)

        self.btn_clearlog = ttk.Button(frm_aux, text="🗑 清空日志",
                                       command=self._on_clear_log, width=12)
        self.btn_clearlog.pack(side="left", padx=5)

        self.btn_whitelist = ttk.Button(frm_aux, text="🐟 白名单",
                                        command=self._on_whitelist, width=12)
        self.btn_whitelist.pack(side="left", padx=5)

        self.var_topmost = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm_aux, text="窗口置顶",
                        variable=self.var_topmost,
                        command=self._on_topmost).pack(side="left", padx=5)

        self.var_osc = tk.BooleanVar(value=config.USE_OSC)
        ttk.Checkbutton(frm_aux, text="OSC输入",
                        variable=self.var_osc,
                        command=self._on_osc_toggle).pack(side="left", padx=5)

        self.var_show_debug = tk.BooleanVar(value=config.SHOW_DEBUG)
        ttk.Checkbutton(frm_aux, text="Debug窗口",
                        variable=self.var_show_debug,
                        command=self._on_debug_toggle).pack(side="left", padx=5)

        # ── YOLO 控制区 ──
        frm_yolo = ttk.LabelFrame(self.root, text=" YOLO 目标检测 ")
        frm_yolo.pack(fill="x", **pad)

        config.USE_YOLO = True
        ttk.Label(frm_yolo, text="YOLO 已启用").pack(side="left", padx=5)

        self.var_yolo_collect = tk.BooleanVar(value=config.YOLO_COLLECT)
        ttk.Checkbutton(frm_yolo, text="采集数据",
                        variable=self.var_yolo_collect,
                        command=self._on_yolo_collect_toggle).pack(
                            side="left", padx=5)

        ttk.Label(frm_yolo, text="设备:").pack(side="left", padx=(10, 2))
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

        self.btn_roi = ttk.Button(frm_roi, text="📐 框选检测区域",
                                  command=self._on_select_roi, width=15)
        self.btn_roi.pack(side="left", padx=5)

        self.btn_clear_roi = ttk.Button(frm_roi, text="✕ 清除区域",
                                        command=self._on_clear_roi, width=12)
        self.btn_clear_roi.pack(side="left", padx=5)

        self.var_roi = tk.StringVar(value="未设置 (全屏搜索)")
        ttk.Label(frm_roi, text="检测区域:").pack(side="left", padx=(10, 2))
        self.lbl_roi = ttk.Label(frm_roi, textvariable=self.var_roi,
                                 foreground="gray")
        self.lbl_roi.pack(side="left")

        # (行为克隆 UI 已移除)

        # ── 参数调节面板 ──
        self._build_params_panel(pad)

        # ── 底部：日志 ──
        frm_log = ttk.LabelFrame(self.root, text=" 日志 ")
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
        """构建小游戏参数实时调节面板"""
        frm = ttk.LabelFrame(self.root, text=" 小游戏参数 (实时生效) ")
        frm.pack(fill="x", **pad)

        # 4列布局: [标签 输入框] [标签 输入框]
        cols_per_row = 2
        gpad = {"padx": 4, "pady": 2}

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

        ttk.Button(btn_frame, text="应用参数",
                   command=self._apply_params, width=10).pack(side="left", padx=3)
        ttk.Button(btn_frame, text="恢复默认",
                   command=self._reset_params, width=10).pack(side="left", padx=3)

    def _config_to_display(self, attr: str, vtype: str) -> str:
        """将 config 值转换为 GUI 显示值"""
        val = getattr(config, attr)
        if vtype == "ms":
            return str(round(val * 1000))       # 秒 → 毫秒
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
            self._log_msg(f"[参数] 已更新并保存: {', '.join(changed)}")

    def _reset_params(self):
        """恢复所有参数到默认值并删除配置文件"""
        defaults = {
            "BITE_FORCE_HOOK":  15.0,
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
            "SHAKE_HEAD_TIME":  0.01,
            "INITIAL_PRESS_TIME": 0.2,
            "VERIFY_CONSECUTIVE": 2,
        }

        for attr, default_val in defaults.items():
            setattr(config, attr, default_val)
            if attr in self._param_vars:
                var, vtype = self._param_vars[attr]
                var.set(self._config_to_display(attr, vtype))

        # 删除配置文件
        try:
            import os
            if os.path.exists(config.SETTINGS_FILE):
                os.remove(config.SETTINGS_FILE)
        except Exception:
            pass
        self._log_msg("[参数] 已恢复默认值")

    # ══════════════════════════════════════════════════════
    #  参数持久化
    # ══════════════════════════════════════════════════════

    def _save_settings(self):
        """将当前可调参数保存到 settings.json"""
        data = {}
        for attr, (_, vtype) in self._param_vars.items():
            data[attr] = getattr(config, attr)
        data["USE_OSC"] = config.USE_OSC
        data["DETECT_ROI"] = config.DETECT_ROI
        data["YOLO_COLLECT"] = config.YOLO_COLLECT
        data["YOLO_DEVICE"] = config.YOLO_DEVICE
        data["SHOW_DEBUG"] = config.SHOW_DEBUG
        data["FISH_WHITELIST"] = config.FISH_WHITELIST
        try:
            with open(config.SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self._log_msg(f"[警告] 保存配置失败: {e}")

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
                if attr == "USE_OSC":
                    config.USE_OSC = bool(val)
                    if hasattr(self, 'var_osc'):
                        self.var_osc.set(config.USE_OSC)
                    loaded.append(attr)
                elif attr == "DETECT_ROI":
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
                elif attr in self._param_vars:
                    setattr(config, attr, val)
                    var, vtype = self._param_vars[attr]
                    var.set(self._config_to_display(attr, vtype))
                    loaded.append(attr)
            if loaded:
                pass
        except Exception as e:
            self._log_msg(f"[警告] 加载配置失败: {e}")

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
            self._log_msg("[错误] 程序所在路径包含中文或特殊字符，会导致图片/模型加载失败！")
            self._log_msg(f"  当前路径: {config.BASE_DIR}")
            self._log_msg("  请将程序移动到纯英文路径下再运行，例如: D:\\fish")
            return

        # 先尝试连接窗口
        if not self.bot.window.is_valid():
            if not self.bot.window.find():
                self._log_msg("[错误] 未找到 VRChat 窗口！请确保游戏正在运行。")
                return

        self.var_window.set(f"{self.bot.window.title} (HWND={self.bot.window.hwnd})")

        # ★ 开始前应用一次参数 (确保 GUI 上的值生效)
        self._apply_params()

        self.bot.running = True
        self.bot.state = "运行中"

        # 启动后台线程
        if self.bot_thread is None or not self.bot_thread.is_alive():
            self.bot_thread = threading.Thread(target=self.bot.run, daemon=True)
            self.bot_thread.start()

        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self._log_msg("[系统] ▶ 开始自动钓鱼")

    def _on_stop(self):
        """停止钓鱼"""
        self.bot.running = False
        self.bot.input.safe_release()
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self._log_msg("[系统] ■ 已停止")
        self._save_log()

    def _on_toggle_debug(self):
        """切换调试模式"""
        self.bot.debug_mode = not self.bot.debug_mode
        tag = "开启" if self.bot.debug_mode else "关闭"
        self.var_debug.set(tag)
        self._log_msg(f"[系统] 调试模式: {tag}")
        if self.bot.debug_mode:
            self._log_msg("[提示] 调试截图将保存到 debug/ 目录，检测器将输出置信度")

    def _on_connect(self):
        """手动连接 VRChat 窗口"""
        if self.bot.window.find():
            self.var_window.set(
                f"{self.bot.window.title} (HWND={self.bot.window.hwnd})"
            )
            # 重置截图方式，下次截图时重新检测 PrintWindow
            self.bot.screen.reset_capture_method()
            self._log_msg(f"[系统] 已连接: {self.bot.window.title}")
        else:
            self.var_window.set("未找到")
            self._log_msg("[错误] 未找到 VRChat 窗口")

    def _on_screenshot(self):
        """手动保存当前截图（调试用）"""
        if not self.bot.window.is_valid():
            if not self.bot.window.find():
                self._log_msg("[错误] 无法截图: 未连接 VRChat 窗口")
                return

        img, region = self.screen_capture_safe()
        if img is not None:
            self.bot.screen.save_debug(img, "manual_screenshot")
            h, w = img.shape[:2]
            self._log_msg(f"[截图] 已保存 ({w}×{h}) → debug/manual_screenshot.png")
            if region:
                self._log_msg(f"       窗口区域: x={region[0]} y={region[1]} w={region[2]} h={region[3]}")
        else:
            self._log_msg("[错误] 截图失败")

    def _on_clear_log(self):
        """清空日志文本框"""
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.config(state="disabled")

    def _on_whitelist(self):
        """弹窗: 勾选要钓的鱼种"""
        FISH_NAMES = [
            ("fish_black",   "黑鱼"),
            ("fish_white",   "白鱼"),
            ("fish_copper",  "铜鱼"),
            ("fish_green",   "绿鱼"),
            ("fish_blue",    "蓝鱼"),
            ("fish_purple",  "紫鱼"),
            ("fish_pink",    "粉鱼"),
            ("fish_red",     "红鱼"),
            ("fish_rainbow", "彩鱼"),
        ]
        win = tk.Toplevel(self.root)
        win.title("钓鱼白名单")
        win.geometry("200x320")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        ttk.Label(win, text="勾选要钓的鱼:").pack(pady=(10, 5))

        wl = config.FISH_WHITELIST
        chk_vars = {}
        for key, name in FISH_NAMES:
            var = tk.BooleanVar(value=wl.get(key, True))
            chk_vars[key] = var
            ttk.Checkbutton(win, text=name, variable=var).pack(
                anchor="w", padx=30)

        def _apply():
            for key, var in chk_vars.items():
                config.FISH_WHITELIST[key] = var.get()
            self._save_settings()
            enabled = [n for (k, n) in FISH_NAMES if chk_vars[k].get()]
            self._log_msg(f"[白名单] 已更新: {', '.join(enabled)}")
            win.destroy()

        ttk.Button(win, text="确定", command=_apply).pack(pady=10)

    def _on_topmost(self):
        """切换窗口置顶 (用 int 0/1 确保兼容性)"""
        topmost = self.var_topmost.get()
        self.root.wm_attributes("-topmost", 1 if topmost else 0)
        if not topmost:
            self.root.lift()
            self.root.focus_force()

    def _on_osc_toggle(self):
        """切换 OSC / PostMessage 输入模式 (下次启动生效)"""
        use_osc = self.var_osc.get()
        config.USE_OSC = use_osc
        self._save_settings()
        mode = "OSC (VRChat OSC API)" if use_osc else "PostMessage (Win32)"
        self._log_msg(f"[输入] 已切换为 {mode} — 点击「开始」后生效")
        if use_osc:
            self._log_msg("[输入] 请确保 VRChat 已开启 OSC (圆盘菜单 → OSC → 启用)")

    def _on_debug_toggle(self):
        """切换 debug 窗口显示"""
        config.SHOW_DEBUG = self.var_show_debug.get()
        self._save_settings()
        state = "开启" if config.SHOW_DEBUG else "关闭 (提升性能)"
        self._log_msg(f"[Debug] 调试窗口: {state}")
        if not config.SHOW_DEBUG:
            try:
                import cv2
                cv2.destroyWindow("Debug Overlay")
            except Exception:
                pass

    def _preload_yolo(self):
        """后台线程预加载 YOLO 模型，避免阻塞 GUI"""
        def _load():
            try:
                from core.bot import _get_yolo_detector
                self.bot.yolo = _get_yolo_detector()
                pass
            except Exception as e:
                self._log_msg(f"[YOLO] 预加载失败: {e}")
        t = threading.Thread(target=_load, daemon=True)
        t.start()

    def _on_yolo_collect_toggle(self):
        """切换 YOLO 数据采集模式"""
        collect = self.var_yolo_collect.get()
        config.YOLO_COLLECT = collect
        self._save_settings()
        if collect:
            self._log_msg(
                "[YOLO] 数据采集已开启 — 钓鱼时将自动保存截图"
            )
        else:
            self._log_msg("[YOLO] 数据采集已关闭")

    def _on_yolo_device_change(self, _event=None):
        """切换 YOLO 推理设备"""
        dev = self.var_yolo_device.get()
        config.YOLO_DEVICE = dev
        self._save_settings()
        labels = {"auto": "自动 (优先GPU)", "cpu": "CPU (不占显卡)",
                  "gpu": "GPU (需要CUDA)"}
        self._log_msg(f"[YOLO] 设备已切换: {labels.get(dev, dev)} — 下次启动生效")

    def _update_yolo_status(self):
        """更新 YOLO 状态显示"""
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
        if model_ok:
            parts.append("模型 ✓")
        else:
            parts.append("模型 ✗")
        parts.append(f"训练:{n_train}")
        parts.append(f"未标:{n_unlabeled}")
        self.var_yolo_status.set(" | ".join(parts))

    def _on_select_roi(self):
        """框选钓鱼UI检测区域"""
        if not self.bot.window.is_valid():
            if not self.bot.window.find():
                self._log_msg("[错误] 请先连接 VRChat 窗口")
                return

        img, _ = self.screen_capture_safe()
        if img is None:
            self._log_msg("[错误] 截图失败, 无法框选")
            return

        self._log_msg(
            "[框选] 请在弹出窗口中用鼠标框选钓鱼UI区域, "
            "按回车确认, 按ESC取消"
        )

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
            self._log_msg(
                f"[框选] ✓ 检测区域已设置: X={x} Y={y} {w_r}x{h_r}"
            )
        else:
            self._log_msg("[框选] 已取消 (区域太小或按了ESC)")

    def _on_clear_roi(self):
        """清除框选区域"""
        config.DETECT_ROI = None
        self._save_settings()
        self.var_roi.set("未设置 (全屏搜索)")
        self.lbl_roi.config(foreground="gray")
        self._log_msg("[框选] 已清除检测区域, 将使用全屏搜索")

    def screen_capture_safe(self):
        """安全截取屏幕"""
        try:
            return self.bot.screen.grab_window(self.bot.window)
        except Exception as e:
            self._log_msg(f"[错误] 截图异常: {e}")
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
        self._log_msg(f"[系统] 日志已保存 → {path}")
