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
#  分组格式:
#  (分组名, 分组说明, [(显示名, config属性名, 类型, 单位提示), ...])
#  类型: "int" / "float" / "ms" / "pct"
# ═══════════════════════════════════════════════════════════
PARAM_GROUPS = [
    (
        "基础节奏",
        "控制抛竿、提竿、开局按压和每轮结束后的节奏。",
        [
            ("提竿时间(s)", "BITE_FORCE_HOOK", "float", "N 秒后提竿"),
            ("按压时间(s)", "INITIAL_PRESS_TIME", "float", "小游戏正式接管时的开局按压时长"),
            ("归正时间(s)", "POST_CATCH_DELAY", "float", "钓鱼结束或失败后等待多久再抛竿"),
        ],
    ),
    (
        "白条上升控制",
        "白条上升太慢时优先调这里。上升慢: 先加大“最长按住”或“按住增益”; 上升过猛: 反向调小。",
        [
            ("最长按住(ms)", "HOLD_MAX_S", "ms", "单次按住的最大时长, 越大白条越容易上冲"),
            ("按住增益", "HOLD_GAIN", "float", "鱼离中心越远, 会额外增加多少按住时长"),
            ("前瞻时间(s)", "PREDICT_AHEAD", "float", "提前预测鱼的位置, 适合应对快速移动"),
        ],
    ),
    (
        "白条下降控制",
        "白条下降太快、太飘、回落手感不对时调这里。下降太快兜不住: 先加大“抗重力基准”; 太悬浮: 调小它。",
        [
            ("最短按住/抗重力基准(ms)", "HOLD_MIN_S", "ms", "基础托举力度, 也是单次最短按住时长, 越大白条越不容易快速下坠"),
            ("速度阻尼", "SPEED_DAMPING", "float", "下坠快时自动补按, 上升快时自动减按"),
            ("速度平滑", "VELOCITY_SMOOTH", "float", "速度估计平滑度, 越大越稳但反应略慢"),
            ("死区(px)", "DEAD_ZONE", "int", "鱼靠近中心时允许的误差范围, 越大越不容易频繁点按"),
        ],
    ),
    (
        "识别与跟踪",
        "识别不稳、经常跟丢、鱼条对不上时调这里。",
        [
            ("鱼像素大小", "FISH_GAME_SIZE", "int", "游戏内鱼图标的大致像素, 越小搜索倍率越高"),
            ("最大距离(px)", "MAX_FISH_BAR_DIST", "int", "鱼和白条距离超过该值时视为误检"),
            ("旋转阈值(°)", "TRACK_MIN_ANGLE", "float", "轨道倾斜超过此角度时启用旋转校正"),
            ("旋转上限(°)", "TRACK_MAX_ANGLE", "float", "超过此角度通常视为误检, 如海平线"),
            ("搜索上(px)", "REGION_UP", "int", "白条锁定后向上搜索的像素范围"),
            ("搜索下(px)", "REGION_DOWN", "int", "白条锁定后向下搜索的像素范围"),
            ("搜索X(px)", "REGION_X", "int", "白条中心左右各 N 像素范围内检测"),
        ],
    ),
    (
        "开始与结束判定",
        "小游戏开始后的结束判定。连续丢失达到设定帧数后会直接结束，不再做二次验证。",
        [
            ("验证帧数", "VERIFY_FRAMES", "int", "主循环连续丢失达到多少帧后直接判定小游戏结束"),
            ("成功阈值(%)", "SUCCESS_PROGRESS", "pct", "进度条超过此百分比判定钓鱼成功"),
        ],
    ),
]

TUNABLE_PARAMS = [
    item
    for _, _, items in PARAM_GROUPS
    for item in items
]


class FishingApp:
    """VRChat 自动钓鱼 — 主窗口"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("VRC auto fish 263302")
        self.root.resizable(True, True)
        self.root.minsize(520, 400)
        self.root.attributes("-topmost", False)

        # ── 机器人实例 ──
        self.bot = FishingBot()
        self.bot_thread: threading.Thread | None = None

        # ── 参数变量 ──
        self._param_vars = {}        # config属性名 → tk.StringVar
        self._param_entries = {}     # config属性名 → ttk.Entry

        # ── 构建界面 ──
        self._build_ui()

        # ── 加载上次保存的参数 ──
        self._load_settings()

        # ── 自适应窗口尺寸 ──
        self._auto_resize()

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

    def _auto_resize(self):
        """根据内容自适应窗口大小，不超过屏幕可用高度"""
        self.root.update_idletasks()
        req_w = max(self.root.winfo_reqwidth(), 580)
        req_h = self.root.winfo_reqheight()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        max_h = screen_h - 80
        final_h = min(req_h + 60, max_h)
        x = (screen_w - req_w) // 2
        y = max((screen_h - final_h) // 2, 0)
        self.root.geometry(f"{req_w}x{final_h}+{x}+{y}")

    # ══════════════════════════════════════════════════════
    #  界面构建
    # ══════════════════════════════════════════════════════

    def _build_ui(self):
        pad = {"padx": 6, "pady": 2}

        # ── 状态面板（紧凑2行） ──
        frm_status = ttk.LabelFrame(self.root, text=" 状态 ")
        frm_status.pack(fill="x", **pad)

        gpad = {"padx": 6, "pady": 1, "sticky": "w"}

        self.var_state = tk.StringVar(value="就绪")
        self.var_window = tk.StringVar(value="未连接")
        self.var_count = tk.StringVar(value="0")
        self.var_debug = tk.StringVar(value="关闭")

        ttk.Label(frm_status, text="状态:").grid(row=0, column=0, **gpad)
        self.lbl_state = ttk.Label(frm_status, textvariable=self.var_state,
                                   foreground="gray")
        self.lbl_state.grid(row=0, column=1, **gpad)
        ttk.Label(frm_status, text="次数:").grid(
            row=0, column=2, padx=(16, 4), pady=1, sticky="w")
        ttk.Label(frm_status, textvariable=self.var_count).grid(
            row=0, column=3, **gpad)
        ttk.Label(frm_status, text="调试:").grid(
            row=0, column=4, padx=(16, 4), pady=1, sticky="w")
        ttk.Label(frm_status, textvariable=self.var_debug).grid(
            row=0, column=5, **gpad)

        ttk.Label(frm_status, text="窗口:").grid(row=1, column=0, **gpad)
        self.lbl_window = ttk.Label(frm_status, textvariable=self.var_window)
        self.lbl_window.grid(row=1, column=1, columnspan=5, **gpad)

        # ── 按钮区（两行紧凑排列） ──
        frm_btn = ttk.Frame(self.root)
        frm_btn.pack(fill="x", **pad)

        row1 = ttk.Frame(frm_btn)
        row1.pack(fill="x")
        self.btn_start = ttk.Button(row1, text="▶ 开始(F9)",
                                    command=self._on_start, width=12)
        self.btn_start.pack(side="left", padx=2, pady=1)
        self.btn_stop = ttk.Button(row1, text="■ 停止(F10)",
                                   command=self._on_stop, width=12,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=2, pady=1)
        self.btn_debug = ttk.Button(row1, text="调试(F11)",
                                    command=self._on_toggle_debug, width=10)
        self.btn_debug.pack(side="left", padx=2, pady=1)
        self.btn_connect = ttk.Button(row1, text="🔗 连接窗口",
                                      command=self._on_connect, width=12)
        self.btn_connect.pack(side="left", padx=2, pady=1)

        row2 = ttk.Frame(frm_btn)
        row2.pack(fill="x")
        self.btn_screenshot = ttk.Button(row2, text="📸 截图",
                                         command=self._on_screenshot, width=8)
        self.btn_screenshot.pack(side="left", padx=2, pady=1)
        self.btn_clearlog = ttk.Button(row2, text="🗑 清日志",
                                       command=self._on_clear_log, width=8)
        self.btn_clearlog.pack(side="left", padx=2, pady=1)
        self.btn_whitelist = ttk.Button(row2, text="🐟 白名单",
                                        command=self._on_whitelist, width=8)
        self.btn_whitelist.pack(side="left", padx=2, pady=1)
        self.btn_roi = ttk.Button(row2, text="📐 框选区域",
                                  command=self._on_select_roi, width=10)
        self.btn_roi.pack(side="left", padx=2, pady=1)
        self.btn_clear_roi = ttk.Button(row2, text="✕ 清除区域",
                                        command=self._on_clear_roi, width=10)
        self.btn_clear_roi.pack(side="left", padx=2, pady=1)

        # ── 开关选项 + ROI 状态 ──
        frm_toggles = ttk.Frame(self.root)
        frm_toggles.pack(fill="x", **pad)

        self.var_topmost = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm_toggles, text="窗口置顶",
                        variable=self.var_topmost,
                        command=self._on_topmost).pack(side="left", padx=4)

        self.var_show_debug = tk.BooleanVar(value=config.SHOW_DEBUG)
        ttk.Checkbutton(frm_toggles, text="Debug窗口",
                        variable=self.var_show_debug,
                        command=self._on_debug_toggle).pack(side="left", padx=4)

        self.var_skip_success = tk.BooleanVar(
            value=getattr(config, "SKIP_SUCCESS_CHECK", False))
        chk_skip = ttk.Checkbutton(frm_toggles, text="跳过成功检查",
                                   variable=self.var_skip_success,
                                   command=self._on_skip_success_toggle)
        chk_skip.pack(side="left", padx=4)
        self._create_tooltip(chk_skip,
            "启用后不再检测成功阈值，无论成功失败都点击两次收杆。\n"
            "因为游戏成功需要点一次才能收杆，失败则不用点。\n"
            "很多人反馈在成功判定处卡住，开启此选项可避免。")

        ttk.Label(frm_toggles, text="区域:").pack(side="left", padx=(10, 2))
        self.var_roi = tk.StringVar(value="未设置 (全屏搜索)")
        self.lbl_roi = ttk.Label(frm_toggles, textvariable=self.var_roi,
                                 foreground="gray")
        self.lbl_roi.pack(side="left")

        # ── 防卡杆（紧凑单行） ──
        frm_anti = ttk.LabelFrame(self.root, text=" 防卡杆（需要开启OSC） ")
        frm_anti.pack(fill="x", **pad)

        row_anti = ttk.Frame(frm_anti)
        row_anti.pack(fill="x", padx=4, pady=2)

        self.var_anti_mode = tk.StringVar(
            value=getattr(config, "ANTI_STUCK_MODE", "jump"))
        rb_shake = ttk.Radiobutton(row_anti, text="摇头",
                                   variable=self.var_anti_mode, value="shake",
                                   command=self._on_anti_mode_change)
        rb_shake.pack(side="left", padx=4)
        self._create_tooltip(rb_shake, "抛竿前通过OSC左右摇头，防止长时间挂机卡杆")

        rb_jump = ttk.Radiobutton(row_anti, text="跳跃",
                                  variable=self.var_anti_mode, value="jump",
                                  command=self._on_anti_mode_change)
        rb_jump.pack(side="left", padx=4)
        self._create_tooltip(rb_jump,
            "抛竿前通过OSC发送/input/Jump，跳一下防卡杆\n"
            "和摇头一样纯OSC通信，不需要聚焦窗口")

        ttk.Label(row_anti, text="摇头时长(s)").pack(side="left", padx=(12, 2))
        self.var_shake_time = tk.StringVar(
            value=f"{config.SHAKE_HEAD_TIME:.3f}")
        ent_shake = ttk.Entry(row_anti, textvariable=self.var_shake_time,
                              width=6, justify="center")
        ent_shake.pack(side="left", padx=2)
        ent_shake.bind("<Return>", lambda e: self._apply_anti_params())
        ent_shake.bind("<FocusOut>", lambda e: self._apply_anti_params())
        self._create_tooltip(ent_shake, "摇头每段按住时长(秒), 0=不摇头")

        # ── YOLO 控制区 ──
        frm_yolo = ttk.LabelFrame(self.root, text=" YOLO 目标检测 ")
        frm_yolo.pack(fill="x", **pad)

        config.USE_YOLO = True
        ttk.Label(frm_yolo, text="YOLO 已启用").pack(side="left", padx=4)

        self.var_yolo_collect = tk.BooleanVar(value=config.YOLO_COLLECT)
        ttk.Checkbutton(frm_yolo, text="采集数据",
                        variable=self.var_yolo_collect,
                        command=self._on_yolo_collect_toggle).pack(
                            side="left", padx=4)

        ttk.Label(frm_yolo, text="设备:").pack(side="left", padx=(8, 2))
        self.var_yolo_device = tk.StringVar(value=config.YOLO_DEVICE)
        cmb_dev = ttk.Combobox(frm_yolo, textvariable=self.var_yolo_device,
                               values=["auto", "cpu", "gpu"],
                               state="readonly", width=5)
        cmb_dev.pack(side="left", padx=2)
        cmb_dev.bind("<<ComboboxSelected>>", self._on_yolo_device_change)

        self.var_yolo_status = tk.StringVar(value="")
        self._update_yolo_status()
        ttk.Label(frm_yolo, textvariable=self.var_yolo_status,
                  foreground="gray").pack(side="left", padx=8)

        # ── 参数调节面板 ──
        self._build_params_panel(pad)

        # ── 底部：日志 ──
        frm_log = ttk.LabelFrame(self.root, text=" 日志 ")
        frm_log.pack(fill="both", expand=True, **pad)

        self.txt_log = scrolledtext.ScrolledText(
            frm_log, height=10, state="disabled",
            font=("Consolas", 9), wrap="word",
            bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="#d4d4d4",
        )
        self.txt_log.pack(fill="both", expand=True, padx=4, pady=4)

    # ══════════════════════════════════════════════════════
    #  参数调节面板
    # ══════════════════════════════════════════════════════

    def _build_params_panel(self, pad):
        """构建小游戏参数实时调节面板"""
        self.frm_params = ttk.LabelFrame(self.root, text=" 小游戏参数 (实时生效) ")
        self.frm_params.pack(fill="x", **pad)

        header = ttk.Frame(self.frm_params)
        header.pack(fill="x", padx=6, pady=(4, 0))

        self.var_grouped_params = tk.BooleanVar(value=True)
        chk_layout = ttk.Checkbutton(
            header,
            text="新版分类界面",
            variable=self.var_grouped_params,
            command=self._on_params_layout_toggle,
        )
        chk_layout.pack(side="right")
        self._create_tooltip(
            chk_layout,
            "开启: 使用新版分类参数界面\n关闭: 使用旧版平铺参数界面",
        )

        self.frm_params_body = ttk.Frame(self.frm_params)
        self.frm_params_body.pack(fill="x", expand=True)
        self._render_params_panel()

    def _render_params_panel(self):
        """根据当前开关渲染新版/旧版参数界面"""
        for child in self.frm_params_body.winfo_children():
            child.destroy()

        self._param_vars = {}
        self._param_entries = {}

        if self.var_grouped_params.get():
            self._render_grouped_params_panel()
        else:
            self._render_legacy_params_panel()

        self._update_success_threshold_state()

        btn_frame = ttk.Frame(self.frm_params_body)
        btn_frame.pack(fill="x", padx=6, pady=(0, 4))

        ttk.Button(btn_frame, text="应用参数",
                   command=self._apply_params, width=10).pack(side="right", padx=2)
        ttk.Button(btn_frame, text="恢复默认",
                   command=self._reset_params, width=10).pack(side="right", padx=2)

    def _render_grouped_params_panel(self):
        notebook = ttk.Notebook(self.frm_params_body)
        notebook.pack(fill="x", expand=True, padx=4, pady=3)

        cols_per_row = 2
        gpad = {"padx": 3, "pady": 2}

        for group_name, group_help, items in PARAM_GROUPS:
            tab = ttk.Frame(notebook)
            notebook.add(tab, text=group_name)

            lbl_help = ttk.Label(
                tab,
                text=group_help,
                foreground="gray",
                justify="left",
                wraplength=520,
            )
            lbl_help.pack(fill="x", padx=6, pady=(4, 2))

            grid = ttk.Frame(tab)
            grid.pack(fill="x", padx=4, pady=(0, 4))

            for i, (label, attr, vtype, tip) in enumerate(items):
                row = i // cols_per_row
                col_base = (i % cols_per_row) * 2
                self._create_param_entry(
                    grid, row, col_base, label, attr, vtype, tip,
                    label_width=12, entry_width=8, gpad=gpad
                )

    def _render_legacy_params_panel(self):
        grid = ttk.Frame(self.frm_params_body)
        grid.pack(fill="x", padx=6, pady=4)

        cols_per_row = 3
        gpad = {"padx": 3, "pady": 1}

        for i, (label, attr, vtype, tip) in enumerate(TUNABLE_PARAMS):
            row = i // cols_per_row
            col_base = (i % cols_per_row) * 2
            self._create_param_entry(
                grid, row, col_base, label, attr, vtype, tip,
                label_width=10, entry_width=6, gpad=gpad
            )

    def _create_param_entry(self, parent, row, col_base, label, attr, vtype, tip,
                            label_width, entry_width, gpad):
        display_val = self._config_to_display(attr, vtype)
        var = tk.StringVar(value=display_val)
        self._param_vars[attr] = (var, vtype)

        lbl = ttk.Label(parent, text=label, width=label_width, anchor="e")
        lbl.grid(row=row, column=col_base, sticky="e", **gpad)

        entry = ttk.Entry(parent, textvariable=var, width=entry_width,
                          justify="center")
        entry.grid(row=row, column=col_base + 1, sticky="w", **gpad)
        self._param_entries[attr] = entry

        entry.bind("<Return>", lambda e: self._apply_params())
        entry.bind("<FocusOut>", lambda e: self._apply_params())

        if tip:
            self._create_tooltip(entry, tip)

    def _on_params_layout_toggle(self):
        """切换新版分类/旧版平铺参数界面"""
        self._render_params_panel()
        self._save_settings()
        self._auto_resize()
        mode = "新版分类界面" if self.var_grouped_params.get() else "旧版平铺界面"
        self._log_msg(f"[界面] 参数面板已切换为: {mode}")

    def _update_success_threshold_state(self):
        """根据跳过成功检查开关，禁用/启用成功阈值输入框"""
        entry = self._param_entries.get("SUCCESS_PROGRESS")
        if entry is None:
            return
        if getattr(config, "SKIP_SUCCESS_CHECK", False):
            entry.state(["disabled"])
        else:
            entry.state(["!disabled"])

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
            self._log_msg(f"[参数] 已更新并保存: {', '.join(changed)}")

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
            "VERIFY_FRAMES": 5,
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
        self._update_success_threshold_state()
        config.ANTI_STUCK_MODE = "jump"
        if hasattr(self, 'var_anti_mode'):
            self.var_anti_mode.set("jump")
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
        self._log_msg("[参数] 已恢复默认值")

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
        data["GROUPED_PARAMS_UI"] = self.var_grouped_params.get()
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
            # 允许用户保存更小的最长/最短按住，只拦截非正数这类明显异常值。
            if data.get("HOLD_MAX_S", 1) <= 0:
                data["HOLD_MAX_S"] = 0.100
            if data.get("HOLD_MIN_S", 1) <= 0:
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
                    self._update_success_threshold_state()
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
                elif attr == "GROUPED_PARAMS_UI":
                    if hasattr(self, 'var_grouped_params'):
                        self.var_grouped_params.set(bool(val))
                        self._render_params_panel()
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
        self.btn_roi.config(state="disabled")
        self.btn_clear_roi.config(state="disabled")
        self._log_msg("[系统] ▶ 开始自动钓鱼")

    def _on_stop(self):
        """停止钓鱼"""
        self.bot.running = False
        self.bot._force_minigame = False
        self.bot.input.safe_release()
        self.bot.shutdown_debug_overlay()
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.btn_roi.config(state="normal")
        self.btn_clear_roi.config(state="normal")
        self._log_msg("[系统] ■ 已停止")
        self._save_log_async()

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
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        ttk.Label(win, text="勾选要钓的鱼:").pack(pady=(10, 5))

        wl = config.FISH_WHITELIST
        chk_vars = {}
        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        for col in range(2):
            body.columnconfigure(col, weight=1)

        for i, (key, name) in enumerate(FISH_NAMES):
            var = tk.BooleanVar(value=wl.get(key, True))
            chk_vars[key] = var
            row = i // 2
            col = i % 2
            ttk.Checkbutton(body, text=name, variable=var).grid(
                row=row, column=col, sticky="w", padx=12, pady=4)

        def _apply():
            for key, var in chk_vars.items():
                config.FISH_WHITELIST[key] = var.get()
            self._save_settings()
            enabled = [n for (k, n) in FISH_NAMES if chk_vars[k].get()]
            self._log_msg(f"[白名单] 已更新: {', '.join(enabled)}")
            win.destroy()

        ttk.Button(win, text="确定", command=_apply).pack(pady=10)
        win.update_idletasks()
        req_w = max(win.winfo_reqwidth() + 20, 260)
        req_h = max(win.winfo_reqheight() + 10, 240)
        screen_w = win.winfo_screenwidth()
        screen_h = win.winfo_screenheight()
        final_w = min(req_w, screen_w - 80)
        final_h = min(req_h, screen_h - 80)
        x = max((screen_w - final_w) // 2, 0)
        y = max((screen_h - final_h) // 2, 0)
        win.geometry(f"{final_w}x{final_h}+{x}+{y}")

    def _on_topmost(self):
        """切换窗口置顶 (用 int 0/1 确保兼容性)"""
        topmost = self.var_topmost.get()
        self.root.wm_attributes("-topmost", 1 if topmost else 0)
        if not topmost:
            self.root.lift()
            self.root.focus_force()

    def _on_debug_toggle(self):
        """切换 debug 窗口显示"""
        config.SHOW_DEBUG = self.var_show_debug.get()
        self._save_settings()
        state = "开启" if config.SHOW_DEBUG else "关闭 (提升性能)"
        self._log_msg(f"[Debug] 调试窗口: {state}")
        if not config.SHOW_DEBUG:
            self.bot.shutdown_debug_overlay()

    def _on_skip_success_toggle(self):
        """切换 跳过成功检查"""
        config.SKIP_SUCCESS_CHECK = self.var_skip_success.get()
        self._update_success_threshold_state()
        self._save_settings()
        state = "开启 (跳过最终进度判定)" if config.SKIP_SUCCESS_CHECK else "关闭"
        self._log_msg(f"[设置] 跳过成功检查: {state}")

    def _on_anti_mode_change(self):
        """切换 防卡杆模式"""
        mode = self.var_anti_mode.get()
        config.ANTI_STUCK_MODE = mode
        self._save_settings()
        labels = {"shake": "摇头", "jump": "跳跃"}
        self._log_msg(f"[设置] 防卡杆方式: {labels.get(mode, mode)}")

    def _apply_anti_params(self):
        """应用防卡杆参数 (摇头时长)"""
        changed = []
        try:
            val = float(self.var_shake_time.get().strip())
            if abs(val - config.SHAKE_HEAD_TIME) > 1e-6:
                config.SHAKE_HEAD_TIME = val
                changed.append(f"摇头时长={val:.3f}s")
        except ValueError:
            pass

        if changed:
            self._save_settings()
            self._log_msg(f"[防卡杆] 已更新: {', '.join(changed)}")

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

        # ★ 在独立线程中运行 cv2.selectROI，避免与 keyboard 库的
        #   Win32 Hook 线程争抢 GIL 导致 Fatal Python error。
        def _select_worker(snap):
            win_name = "Select Fishing ROI - Enter=OK / Esc=Cancel"
            cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
            h, w = snap.shape[:2]
            dw = min(w, 1280)
            dh = int(h * dw / w)
            cv2.resizeWindow(win_name, dw, dh)

            roi = cv2.selectROI(win_name, snap,
                                fromCenter=False, showCrosshair=True)
            cv2.destroyAllWindows()

            x, y, w_r, h_r = [int(v) for v in roi]
            if w_r > 10 and h_r > 10:
                config.DETECT_ROI = [x, y, w_r, h_r]
                self._save_settings()
                # tkinter 不是线程安全的，用 after() 回到主线程更新
                self.master.after(0, lambda: self.var_roi.set(
                    f"X={x} Y={y} {w_r}x{h_r}"))
                self.master.after(0, lambda: self.lbl_roi.config(
                    foreground="green"))
                self._log_msg(
                    f"[框选] ✓ 检测区域已设置: X={x} Y={y} {w_r}x{h_r}"
                )
            else:
                self._log_msg("[框选] 已取消 (区域太小或按了ESC)")

        threading.Thread(target=_select_worker, args=(img,),
                         daemon=True, name="ROISelect").start()

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
        self.bot._force_minigame = False
        self.bot.input.safe_release()
        self.bot.shutdown_debug_overlay()
        self._save_settings()
        self._save_log()
        self.root.destroy()

    def _save_log(self):
        """保存日志到文件 (覆盖上一次)"""
        import os
        path = os.path.join(config.DEBUG_DIR, "last_run.log")
        log.save(path)
        self._log_msg(f"[系统] 日志已保存 → {path}")

    def _save_log_async(self):
        """后台保存日志，避免停止按钮阻塞 GUI"""
        path = os.path.join(config.DEBUG_DIR, "last_run.log")

        def _worker():
            log.save(path)
            try:
                self.root.after(0, lambda: self._log_msg(f"[系统] 日志已保存 → {path}"))
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()
