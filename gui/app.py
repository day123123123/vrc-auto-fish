"""
tkinter GUI 模块
================
主控制面板：状态显示、控制按钮、参数调节、实时日志输出。
Bot 在后台线程运行，GUI 通过共享属性 + 日志队列通信。
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import keyboard

import config
from core.bot import FishingBot
from gui.panels import (
    build_control_panel,
    build_status_panel,
    build_toggle_panel,
    build_yolo_panel,
)
from gui.runtime_controller import AppRuntimeController
from gui.settings_store import AppSettingsStore
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
        "白条下降太快、太飘、回落手感不对时调这里。下降太快兜不住: 先加大“最短按住/抗重力基准”; 太悬浮: 调小它。",
        [
            ("最短按住(ms) / 抗重力基准", "HOLD_MIN_S", "ms", "基础托举力度, 也是单次最短按住时长, 越大白条越不容易快速下坠"),
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

PARAM_DEFAULTS = {
    "BITE_FORCE_HOOK": 18.0,
    "FISH_GAME_SIZE": 20,
    "DEAD_ZONE": 15,
    "HOLD_MIN_S": 0.005,
    "HOLD_MAX_S": 0.100,
    "HOLD_GAIN": 0.040,
    "PREDICT_AHEAD": 0.5,
    "SPEED_DAMPING": 0.00025,
    "MAX_FISH_BAR_DIST": 300,
    "VELOCITY_SMOOTH": 0.5,
    "TRACK_MIN_ANGLE": 3.0,
    "TRACK_MAX_ANGLE": 45.0,
    "REGION_UP": 300,
    "REGION_DOWN": 400,
    "REGION_X": 100,
    "POST_CATCH_DELAY": 3.0,
    "INITIAL_PRESS_TIME": 0.2,
    "VERIFY_FRAMES": 5,
    "SUCCESS_PROGRESS": 0.55,
}

SETTINGS_DEFAULTS = {
    "SKIP_SUCCESS_CHECK": False,
    "SYNC_PD_MODE": False,
    "ANTI_STUCK_MODE": "jump",
    "SHAKE_HEAD_TIME": 0.02,
}

PERSISTED_CONFIG_ATTRS = (
    "DETECT_ROI",
    "YOLO_COLLECT",
    "YOLO_DEVICE",
    "SHOW_DEBUG",
    "FISH_WHITELIST",
    "SKIP_SUCCESS_CHECK",
    "SYNC_PD_MODE",
    "ANTI_STUCK_MODE",
    "SHAKE_HEAD_TIME",
)


class FishingApp:
    """VRChat 自动钓鱼 — 主窗口"""

    PARAM_DEFAULTS = PARAM_DEFAULTS
    SETTINGS_DEFAULTS = SETTINGS_DEFAULTS
    PERSISTED_CONFIG_ATTRS = PERSISTED_CONFIG_ATTRS

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
        self.settings_store = AppSettingsStore(self)
        self.runtime = AppRuntimeController(self)
        self.var_preset_name = tk.StringVar(value="")

        # ── 构建界面 ──
        self._build_ui()

        # ── 加载上次保存的参数 ──
        self._load_settings()

        # ── 自适应窗口尺寸 ──
        self._auto_resize()

        # ── 预加载 YOLO ──
        if self.bot.yolo is None:
            self.runtime.preload_yolo()

        # ── 注册全局快捷键 ──
        keyboard.add_hotkey(config.HOTKEY_TOGGLE, self._toggle_from_hotkey)
        keyboard.add_hotkey(config.HOTKEY_STOP,   self._stop_from_hotkey)
        keyboard.add_hotkey(config.HOTKEY_DEBUG,   self._toggle_debug_from_hotkey)

        # ── 启动轮询 ──
        self.runtime.poll()

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
        build_status_panel(self, self.root, pad)
        build_control_panel(self, self.root, pad)
        build_toggle_panel(self, self.root, pad)

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

        build_yolo_panel(self, self.root, pad)

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

        preset_bar = ttk.Frame(header)
        preset_bar.pack(side="left", fill="x", expand=True)

        ttk.Label(preset_bar, text="参数预设").pack(side="left", padx=(0, 4))
        self.cmb_preset = ttk.Combobox(
            preset_bar,
            textvariable=self.var_preset_name,
            state="normal",
            width=18,
        )
        self.cmb_preset.pack(side="left", padx=2)
        self.cmb_preset.bind("<<ComboboxSelected>>", self._on_load_preset)
        self._create_tooltip(
            self.cmb_preset,
            "输入新名字后点“保存预设”可另存一套参数。\n"
            "下拉选择已有预设后点“加载预设”可快速切换。",
        )

        ttk.Button(
            preset_bar, text="加载预设", command=self._on_load_preset, width=10
        ).pack(side="left", padx=2)
        ttk.Button(
            preset_bar, text="保存预设", command=self._on_save_preset, width=10
        ).pack(side="left", padx=2)
        ttk.Button(
            preset_bar, text="删除预设", command=self._on_delete_preset, width=10
        ).pack(side="left", padx=2)

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
        self._refresh_preset_list()

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
        return self.settings_store.config_to_display(attr, vtype)

    def _display_to_config(self, text: str, vtype: str):
        """将 GUI 显示值转换为 config 值"""
        return self.settings_store.display_to_config(text, vtype)

    def _apply_params(self):
        """读取所有参数输入框，应用到 config 并保存到文件"""
        self.settings_store.apply_params()

    def _refresh_param_widgets(self):
        """把当前 config 参数值回写到界面输入框。"""
        self.settings_store.refresh_param_widgets()

    def _reset_extra_settings(self):
        """恢复非参数输入框类设置的默认值。"""
        self.settings_store.reset_extra_settings()

    def _collect_settings_data(self):
        """收集需要写入 settings.json 的配置。"""
        return self.settings_store.collect_settings_data()

    def _normalize_loaded_settings(self, data: dict):
        """兼容旧配置并清理明显异常值。"""
        self.settings_store.normalize_loaded_settings(data)

    def _apply_loaded_setting(self, attr: str, val) -> bool:
        """应用单项持久化设置，返回是否成功处理。"""
        return self.settings_store.apply_loaded_setting(attr, val)

    def _apply_bool_setting(self, config_attr: str, value: bool,
                            log_on: str, log_off: str,
                            after_change=None):
        """统一处理布尔设置的写入、保存与日志。"""
        setattr(config, config_attr, bool(value))
        self._save_settings()
        self._log_msg(log_on if value else log_off)
        if after_change is not None:
            after_change(bool(value))

    def _apply_choice_setting(self, config_attr: str, value,
                              log_message: str):
        """统一处理枚举/字符串设置。"""
        setattr(config, config_attr, value)
        self._save_settings()
        self._log_msg(log_message)

    def _reset_params(self):
        """恢复所有参数到默认值并删除配置文件"""
        self.settings_store.reset_params()

    # ══════════════════════════════════════════════════════
    #  参数持久化
    # ══════════════════════════════════════════════════════

    def _save_settings(self):
        """将当前可调参数保存到 settings.json"""
        try:
            self.settings_store.save()
        except Exception as e:
            self._log_msg(f"[警告] 保存配置失败: {e}")

    def _load_settings(self):
        """启动时从 settings.json 加载参数"""
        try:
            self.settings_store.load()
            self._refresh_preset_list()
        except Exception as e:
            self._log_msg(f"[警告] 加载配置失败: {e}")

    def _refresh_preset_list(self):
        """刷新预设下拉列表。"""
        names = self.settings_store.get_preset_names()
        if hasattr(self, "cmb_preset"):
            self.cmb_preset["values"] = names
        current_name = self.settings_store.get_active_preset_name()
        if current_name:
            self.var_preset_name.set(current_name)

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
        return AppRuntimeController.has_non_ascii(path)

    def _on_start(self):
        """开始钓鱼"""
        self.runtime.on_start()

    def _on_stop(self):
        """停止钓鱼"""
        self.runtime.on_stop()

    def _on_toggle_debug(self):
        """切换调试模式"""
        self.runtime.on_toggle_debug()

    def _on_connect(self):
        """手动连接 VRChat 窗口"""
        self.runtime.on_connect()

    def _on_screenshot(self):
        """手动保存当前截图（调试用）"""
        self.runtime.on_screenshot()

    def _on_clear_log(self):
        """清空日志文本框"""
        self.runtime.on_clear_log()

    def _on_whitelist(self):
        """弹窗: 勾选要钓的鱼种"""
        self.runtime.on_whitelist()

    def _on_topmost(self):
        """切换窗口置顶 (用 int 0/1 确保兼容性)"""
        self.runtime.on_topmost()

    def _on_debug_toggle(self):
        """切换 debug 窗口显示"""
        value = self.var_show_debug.get()
        self._apply_bool_setting(
            "SHOW_DEBUG",
            value,
            "[Debug] 调试窗口: 开启",
            "[Debug] 调试窗口: 关闭 (提升性能)",
            after_change=lambda enabled: (
                None if enabled else self.bot.shutdown_debug_overlay()
            ),
        )

    def _on_skip_success_toggle(self):
        """切换 跳过成功检查"""
        value = self.var_skip_success.get()
        self._apply_bool_setting(
            "SKIP_SUCCESS_CHECK",
            value,
            "[设置] 跳过成功检查: 开启 (跳过最终进度判定)",
            "[设置] 跳过成功检查: 关闭",
            after_change=lambda _enabled: self._update_success_threshold_state(),
        )

    def _on_sync_pd_mode_toggle(self):
        """切换 旧版模式（使用旧版参数）"""
        self._apply_bool_setting(
            "SYNC_PD_MODE",
            self.var_sync_pd_mode.get(),
            "[设置] 旧版模式（使用旧版参数）: 开启",
            "[设置] 旧版模式（使用旧版参数）: 关闭（异步流水线）",
        )

    def _on_anti_mode_change(self):
        """切换 防卡杆模式"""
        mode = self.var_anti_mode.get()
        labels = {"shake": "摇头", "jump": "跳跃"}
        self._apply_choice_setting(
            "ANTI_STUCK_MODE",
            mode,
            f"[设置] 防卡杆方式: {labels.get(mode, mode)}",
        )

    def _on_load_preset(self, _event=None):
        """加载选中的参数预设。"""
        name = self.var_preset_name.get().strip()
        if not name:
            self._log_msg("[预设] 请输入或选择要加载的预设名")
            return
        if self.settings_store.load_preset(name):
            self._refresh_preset_list()
            self._log_msg(f"[预设] 已加载: {name}")
        else:
            self._log_msg(f"[预设] 未找到: {name}")

    def _on_save_preset(self):
        """保存当前参数为一个预设。"""
        name = self.var_preset_name.get().strip()
        if not name:
            self._log_msg("[预设] 请先输入预设名再保存")
            return
        self.settings_store.apply_params()
        self.settings_store.save_preset(name)
        self._refresh_preset_list()
        self._log_msg(f"[预设] 已保存: {name}")

    def _on_delete_preset(self):
        """删除当前选中的预设。"""
        name = self.var_preset_name.get().strip()
        if not name:
            self._log_msg("[预设] 请先选择要删除的预设")
            return
        if self.settings_store.delete_preset(name):
            self._refresh_preset_list()
            self._log_msg(f"[预设] 已删除: {name}")
            return
        self._log_msg(f"[预设] 无法删除: {name}")

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
        self.runtime.preload_yolo()

    def _on_yolo_collect_toggle(self):
        """切换 YOLO 数据采集模式"""
        collect = self.var_yolo_collect.get()
        self._apply_bool_setting(
            "YOLO_COLLECT",
            collect,
            "[YOLO] 数据采集已开启 — 钓鱼时将自动保存截图",
            "[YOLO] 数据采集已关闭",
        )

    def _on_yolo_device_change(self, _event=None):
        """切换 YOLO 推理设备"""
        dev = self.var_yolo_device.get()
        labels = {"auto": "自动 (优先GPU)", "cpu": "CPU (不占显卡)",
                  "gpu": "GPU (需要CUDA)"}
        self._apply_choice_setting(
            "YOLO_DEVICE",
            dev,
            f"[YOLO] 设备已切换: {labels.get(dev, dev)} — 下次启动生效",
        )

    def _update_yolo_status(self):
        """更新 YOLO 状态显示"""
        self.runtime.update_yolo_status()

    def _on_select_roi(self):
        """框选钓鱼UI检测区域"""
        self.runtime.on_select_roi()

    def _on_clear_roi(self):
        """清除框选区域"""
        self.runtime.on_clear_roi()

    def screen_capture_safe(self):
        """安全截取屏幕"""
        return self.runtime.screen_capture_safe()

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
        self.runtime.poll()

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
        self.runtime.on_close()

    def _save_log(self):
        """保存日志到文件 (覆盖上一次)"""
        self.runtime.save_log()

    def _save_log_async(self):
        """后台保存日志，避免停止按钮阻塞 GUI"""
        self.runtime.save_log_async()
