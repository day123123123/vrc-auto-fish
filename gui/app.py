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
from utils.i18n import available_languages, init_language, set_language, t
from utils.logger import log


# ═══════════════════════════════════════════════════════════
#  可调参数定义
#  分组格式:
#  (分组名, 分组说明, [(显示名, config属性名, 类型, 单位提示), ...])
#  类型: "int" / "float" / "ms" / "pct"
# ═══════════════════════════════════════════════════════════
PARAM_GROUPS = [
    (
        "params.group.basic",
        "params.group.basic.help",
        [
            ("params.BITE_FORCE_HOOK.label", "BITE_FORCE_HOOK", "float", "params.BITE_FORCE_HOOK.tip"),
            ("params.INITIAL_PRESS_TIME.label", "INITIAL_PRESS_TIME", "float", "params.INITIAL_PRESS_TIME.tip"),
            ("params.POST_CATCH_DELAY.label", "POST_CATCH_DELAY", "float", "params.POST_CATCH_DELAY.tip"),
        ],
    ),
    (
        "params.group.barRise",
        "params.group.barRise.help",
        [
            ("params.HOLD_MAX_S.label", "HOLD_MAX_S", "ms", "params.HOLD_MAX_S.tip"),
            ("params.HOLD_GAIN.label", "HOLD_GAIN", "float", "params.HOLD_GAIN.tip"),
            ("params.PREDICT_AHEAD.label", "PREDICT_AHEAD", "float", "params.PREDICT_AHEAD.tip"),
        ],
    ),
    (
        "params.group.barFall",
        "params.group.barFall.help",
        [
            ("params.HOLD_MIN_S.label", "HOLD_MIN_S", "ms", "params.HOLD_MIN_S.tip"),
            ("params.SPEED_DAMPING.label", "SPEED_DAMPING", "float", "params.SPEED_DAMPING.tip"),
            ("params.VELOCITY_SMOOTH.label", "VELOCITY_SMOOTH", "float", "params.VELOCITY_SMOOTH.tip"),
            ("params.DEAD_ZONE.label", "DEAD_ZONE", "int", "params.DEAD_ZONE.tip"),
        ],
    ),
    (
        "params.group.detect",
        "params.group.detect.help",
        [
            ("params.FISH_GAME_SIZE.label", "FISH_GAME_SIZE", "int", "params.FISH_GAME_SIZE.tip"),
            ("params.MAX_FISH_BAR_DIST.label", "MAX_FISH_BAR_DIST", "int", "params.MAX_FISH_BAR_DIST.tip"),
            ("params.TRACK_MIN_ANGLE.label", "TRACK_MIN_ANGLE", "float", "params.TRACK_MIN_ANGLE.tip"),
            ("params.TRACK_MAX_ANGLE.label", "TRACK_MAX_ANGLE", "float", "params.TRACK_MAX_ANGLE.tip"),
            ("params.REGION_UP.label", "REGION_UP", "int", "params.REGION_UP.tip"),
            ("params.REGION_DOWN.label", "REGION_DOWN", "int", "params.REGION_DOWN.tip"),
            ("params.REGION_X.label", "REGION_X", "int", "params.REGION_X.tip"),
        ],
    ),
    (
        "params.group.endJudge",
        "params.group.endJudge.help",
        [
            ("params.VERIFY_FRAMES.label", "VERIFY_FRAMES", "int", "params.VERIFY_FRAMES.tip"),
            ("params.SUCCESS_PROGRESS.label", "SUCCESS_PROGRESS", "pct", "params.SUCCESS_PROGRESS.tip"),
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
    "LANGUAGE",
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

    APP_VERSION = "26031201"

    PARAM_DEFAULTS = PARAM_DEFAULTS
    SETTINGS_DEFAULTS = SETTINGS_DEFAULTS
    PERSISTED_CONFIG_ATTRS = PERSISTED_CONFIG_ATTRS

    def __init__(self, root: tk.Tk):
        self.root = root
        init_language()
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
        self.var_language = tk.StringVar(value="")
        self._language_code_to_label = {}
        self._language_label_to_code = {}

        # ── 构建界面 ──
        self._update_window_title()
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

        self._log_t("log.github")

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

    def tr(self, key: str, default: str | None = None, **kwargs):
        return t(key, default=default, **kwargs)

    def _update_window_title(self):
        self.root.title(self.tr("window.title", version=self.APP_VERSION))

    def _refresh_language_choices(self):
        choices = available_languages()
        self._language_code_to_label = {code: label for code, label in choices}
        self._language_label_to_code = {label: code for code, label in choices}
        self.var_language.set(
            self._language_code_to_label.get(config.LANGUAGE, config.LANGUAGE)
        )

    def _translate_bot_state(self, state_key: str) -> str:
        if not state_key:
            return self.tr("status.ready")
        return self.tr(state_key, default=state_key)

    def _format_roi_text(self):
        if config.DETECT_ROI and len(config.DETECT_ROI) == 4:
            x, y, w, h = config.DETECT_ROI
            return f"X={x} Y={y} {w}x{h}", "green"
        return self.tr("toggle.roiUnset"), "gray"

    def _refresh_live_ui_state(self):
        if hasattr(self, "var_state"):
            self.var_state.set(self._translate_bot_state(self.bot.state))
        if hasattr(self, "var_count"):
            self.var_count.set(str(self.bot.fish_count))
        if hasattr(self, "var_debug"):
            self.var_debug.set(
                self.tr("status.on") if self.bot.debug_mode else self.tr("status.off")
            )
        if hasattr(self, "var_window"):
            if self.bot.window.is_valid():
                self.var_window.set(
                    f"{self.bot.window.title} (HWND={self.bot.window.hwnd})"
                )
            elif not self.var_window.get():
                self.var_window.set(self.tr("status.disconnected"))
        if hasattr(self, "var_roi"):
            roi_text, color = self._format_roi_text()
            self.var_roi.set(roi_text)
            self.lbl_roi.config(foreground=color)
        if hasattr(self, "btn_start"):
            self.btn_start.config(state="disabled" if self.bot.running else "normal")
        if hasattr(self, "btn_stop"):
            self.btn_stop.config(state="normal" if self.bot.running else "disabled")
        if hasattr(self, "btn_roi"):
            self.btn_roi.config(state="disabled" if self.bot.running else "normal")
        if hasattr(self, "btn_clear_roi"):
            self.btn_clear_roi.config(
                state="disabled" if self.bot.running else "normal"
            )
        if hasattr(self, "lbl_state"):
            self.lbl_state.config(
                foreground="green" if self.bot.running else "gray"
            )
        if hasattr(self, "var_show_debug"):
            self.var_show_debug.set(config.SHOW_DEBUG)
        if hasattr(self, "var_skip_success"):
            self.var_skip_success.set(config.SKIP_SUCCESS_CHECK)
        if hasattr(self, "var_sync_pd_mode"):
            self.var_sync_pd_mode.set(config.SYNC_PD_MODE)
        if hasattr(self, "var_anti_mode"):
            self.var_anti_mode.set(getattr(config, "ANTI_STUCK_MODE", "jump"))
        if hasattr(self, "var_shake_time"):
            self.var_shake_time.set(f"{config.SHAKE_HEAD_TIME:.3f}")
        if hasattr(self, "var_yolo_collect"):
            self.var_yolo_collect.set(config.YOLO_COLLECT)
        if hasattr(self, "var_yolo_device"):
            self.var_yolo_device.set(config.YOLO_DEVICE)
        if hasattr(self, "var_grouped_params"):
            self.var_grouped_params.set(self.var_grouped_params.get())
        if hasattr(self, "var_language"):
            self.var_language.set(
                self._language_code_to_label.get(config.LANGUAGE, config.LANGUAGE)
            )
        self._update_yolo_status()

    def _rebuild_ui_for_language(self):
        log_text = ""
        if hasattr(self, "txt_log"):
            log_text = self.txt_log.get("1.0", "end-1c")
        for child in self.root.winfo_children():
            child.destroy()
        self._update_window_title()
        self._build_ui()
        if log_text:
            self.txt_log.config(state="normal")
            self.txt_log.insert("end", log_text + "\n")
            self.txt_log.see("end")
            self.txt_log.config(state="disabled")
        self._refresh_preset_list()
        self._refresh_param_widgets()
        self._refresh_live_ui_state()
        self._update_success_threshold_state()
        self._auto_resize()

    # ══════════════════════════════════════════════════════
    #  界面构建
    # ══════════════════════════════════════════════════════

    def _build_ui(self):
        pad = {"padx": 6, "pady": 2}
        self._refresh_language_choices()
        build_status_panel(self, self.root, pad)
        build_control_panel(self, self.root, pad)
        build_toggle_panel(self, self.root, pad)

        # ── 防卡杆（紧凑单行） ──
        frm_anti = ttk.LabelFrame(self.root, text=self.tr("anti.frame"))
        frm_anti.pack(fill="x", **pad)

        row_anti = ttk.Frame(frm_anti)
        row_anti.pack(fill="x", padx=4, pady=2)

        self.var_anti_mode = tk.StringVar(
            value=getattr(config, "ANTI_STUCK_MODE", "jump"))
        rb_shake = ttk.Radiobutton(row_anti, text=self.tr("anti.mode.shake"),
                                   variable=self.var_anti_mode, value="shake",
                                   command=self._on_anti_mode_change)
        rb_shake.pack(side="left", padx=4)
        self._create_tooltip(rb_shake, self.tr("anti.tooltip.shake"))

        rb_jump = ttk.Radiobutton(row_anti, text=self.tr("anti.mode.jump"),
                                  variable=self.var_anti_mode, value="jump",
                                  command=self._on_anti_mode_change)
        rb_jump.pack(side="left", padx=4)
        self._create_tooltip(rb_jump, self.tr("anti.tooltip.jump"))

        ttk.Label(row_anti, text=self.tr("anti.label.shakeTime")).pack(side="left", padx=(12, 2))
        self.var_shake_time = tk.StringVar(
            value=f"{config.SHAKE_HEAD_TIME:.3f}")
        ent_shake = ttk.Entry(row_anti, textvariable=self.var_shake_time,
                              width=6, justify="center")
        ent_shake.pack(side="left", padx=2)
        ent_shake.bind("<Return>", lambda e: self._apply_anti_params())
        ent_shake.bind("<FocusOut>", lambda e: self._apply_anti_params())
        self._create_tooltip(ent_shake, self.tr("anti.tooltip.shakeTime"))

        build_yolo_panel(self, self.root, pad)

        # ── 参数调节面板 ──
        self._build_params_panel(pad)

        # ── 底部：日志 ──
        frm_log = ttk.LabelFrame(self.root, text=self.tr("log.frame"))
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
        self.frm_params = ttk.LabelFrame(self.root, text=self.tr("params.frame"))
        self.frm_params.pack(fill="x", **pad)

        header = ttk.Frame(self.frm_params)
        header.pack(fill="x", padx=6, pady=(4, 0))

        preset_bar = ttk.Frame(header)
        preset_bar.pack(side="left", fill="x", expand=True)

        ttk.Label(preset_bar, text=self.tr("params.presets")).pack(side="left", padx=(0, 4))
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
            self.tr("params.tooltip.presets"),
        )

        ttk.Button(
            preset_bar, text=self.tr("params.loadPreset"), command=self._on_load_preset, width=10
        ).pack(side="left", padx=2)
        ttk.Button(
            preset_bar, text=self.tr("params.savePreset"), command=self._on_save_preset, width=10
        ).pack(side="left", padx=2)
        ttk.Button(
            preset_bar, text=self.tr("params.deletePreset"), command=self._on_delete_preset, width=10
        ).pack(side="left", padx=2)

        layout_on = (
            self.var_grouped_params.get()
            if hasattr(self, "var_grouped_params")
            else True
        )
        self.var_grouped_params = tk.BooleanVar(value=layout_on)
        chk_layout = ttk.Checkbutton(
            header,
            text=self.tr("params.groupedLayout"),
            variable=self.var_grouped_params,
            command=self._on_params_layout_toggle,
        )
        chk_layout.pack(side="right")
        self._create_tooltip(
            chk_layout,
            self.tr("params.tooltip.layout"),
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

        ttk.Button(btn_frame, text=self.tr("params.apply"),
                   command=self._apply_params, width=10).pack(side="right", padx=2)
        ttk.Button(btn_frame, text=self.tr("params.reset"),
                   command=self._reset_params, width=10).pack(side="right", padx=2)

    def _render_grouped_params_panel(self):
        notebook = ttk.Notebook(self.frm_params_body)
        notebook.pack(fill="x", expand=True, padx=4, pady=3)

        cols_per_row = 2
        gpad = {"padx": 3, "pady": 2}

        for group_name_key, group_help_key, items in PARAM_GROUPS:
            tab = ttk.Frame(notebook)
            notebook.add(tab, text=self.tr(group_name_key))

            lbl_help = ttk.Label(
                tab,
                text=self.tr(group_help_key),
                foreground="gray",
                justify="left",
                wraplength=520,
            )
            lbl_help.pack(fill="x", padx=6, pady=(4, 2))

            grid = ttk.Frame(tab)
            grid.pack(fill="x", padx=4, pady=(0, 4))

            for i, (label_key, attr, vtype, tip_key) in enumerate(items):
                row = i // cols_per_row
                col_base = (i % cols_per_row) * 2
                self._create_param_entry(
                    grid,
                    row,
                    col_base,
                    self.tr(label_key),
                    attr,
                    vtype,
                    self.tr(tip_key),
                    label_width=18, entry_width=8, gpad=gpad
                )

    def _render_legacy_params_panel(self):
        grid = ttk.Frame(self.frm_params_body)
        grid.pack(fill="x", padx=6, pady=4)

        cols_per_row = 3
        gpad = {"padx": 3, "pady": 1}

        for i, (label_key, attr, vtype, tip_key) in enumerate(TUNABLE_PARAMS):
            row = i // cols_per_row
            col_base = (i % cols_per_row) * 2
            self._create_param_entry(
                grid,
                row,
                col_base,
                self.tr(label_key),
                attr,
                vtype,
                self.tr(tip_key),
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
        mode = self.tr(
            "app.layout.grouped"
            if self.var_grouped_params.get()
            else "app.layout.legacy"
        )
        self._log_t("log.paramsPanelSwitched", mode=mode)

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
            self._log_t("log.saveSettingsFailed", error=e)

    def _load_settings(self):
        """启动时从 settings.json 加载参数"""
        try:
            self.settings_store.load()
            self._refresh_preset_list()
        except Exception as e:
            self._log_t("log.loadSettingsFailed", error=e)

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
            self.tr("log.debugWindowOn"),
            self.tr("log.debugWindowOff"),
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
            self.tr("log.skipSuccessOn"),
            self.tr("log.skipSuccessOff"),
            after_change=lambda _enabled: self._update_success_threshold_state(),
        )

    def _on_sync_pd_mode_toggle(self):
        """切换 旧版模式（使用旧版参数）"""
        self._apply_bool_setting(
            "SYNC_PD_MODE",
            self.var_sync_pd_mode.get(),
            self.tr("log.syncPdOn"),
            self.tr("log.syncPdOff"),
        )

    def _on_anti_mode_change(self):
        """切换 防卡杆模式"""
        mode = self.var_anti_mode.get()
        labels = {
            "shake": self.tr("anti.mode.shake"),
            "jump": self.tr("anti.mode.jump"),
        }
        self._apply_choice_setting(
            "ANTI_STUCK_MODE",
            mode,
            self.tr("log.antiModeUpdated", mode=labels.get(mode, mode)),
        )

    def _on_language_change(self, _event=None):
        """切换界面语言并立即重建主要 UI。"""
        selected = self.var_language.get()
        lang = self._language_label_to_code.get(selected, selected)
        lang = set_language(lang)
        self._save_settings()
        self._rebuild_ui_for_language()
        self._log_t(
            "log.languageChanged",
            language=self._language_code_to_label.get(lang, lang),
        )

    def _on_load_preset(self, _event=None):
        """加载选中的参数预设。"""
        name = self.var_preset_name.get().strip()
        if not name:
            self._log_t("log.presetInputForLoad")
            return
        if self.settings_store.load_preset(name):
            self._refresh_preset_list()
            self._log_t("log.presetLoaded", name=name)
        else:
            self._log_t("log.presetNotFound", name=name)

    def _on_save_preset(self):
        """保存当前参数为一个预设。"""
        name = self.var_preset_name.get().strip()
        if not name:
            self._log_t("log.presetInputForSave")
            return
        self.settings_store.apply_params()
        self.settings_store.save_preset(name)
        self._refresh_preset_list()
        self._log_t("log.presetSaved", name=name)

    def _on_delete_preset(self):
        """删除当前选中的预设。"""
        name = self.var_preset_name.get().strip()
        if not name:
            self._log_t("log.presetInputForDelete")
            return
        if self.settings_store.delete_preset(name):
            self._refresh_preset_list()
            self._log_t("log.presetDeleted", name=name)
            return
        self._log_t("log.presetDeleteFailed", name=name)

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
            self._log_t("log.antiUpdated", changes=", ".join(changed))

    def _preload_yolo(self):
        """后台线程预加载 YOLO 模型，避免阻塞 GUI"""
        self.runtime.preload_yolo()

    def _on_yolo_collect_toggle(self):
        """切换 YOLO 数据采集模式"""
        collect = self.var_yolo_collect.get()
        self._apply_bool_setting(
            "YOLO_COLLECT",
            collect,
            self.tr("log.yoloCollectOn"),
            self.tr("log.yoloCollectOff"),
        )

    def _on_yolo_device_change(self, _event=None):
        """切换 YOLO 推理设备"""
        dev = self.var_yolo_device.get()
        labels = {"auto": "auto", "cpu": "cpu", "gpu": "gpu"}
        self._apply_choice_setting(
            "YOLO_DEVICE",
            dev,
            self.tr("log.yoloDeviceChanged", device=labels.get(dev, dev)),
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

    def _log_t(self, key: str, **kwargs):
        self._log_msg(self.tr(key, **kwargs))

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
