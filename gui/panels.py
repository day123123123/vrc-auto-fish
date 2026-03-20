"""
GUI 面板构建辅助
================
抽取 FishingApp 中的基础面板构建代码。
"""

import tkinter as tk
from tkinter import ttk

import config


def build_status_panel(app, parent, pad):
    frm_status = ttk.LabelFrame(parent, text=app.tr("status.frame"))
    frm_status.pack(fill="x", **pad)
    gpad = {"padx": 6, "pady": 1, "sticky": "w"}

    app.var_state = tk.StringVar(value=app.tr("status.ready"))
    app.var_window = tk.StringVar(value=app.tr("status.disconnected"))
    app.var_count = tk.StringVar(value="0")
    app.var_debug = tk.StringVar(value=app.tr("status.off"))

    ttk.Label(frm_status, text=app.tr("status.label.state")).grid(row=0, column=0, **gpad)
    app.lbl_state = ttk.Label(frm_status, textvariable=app.var_state, foreground="gray")
    app.lbl_state.grid(row=0, column=1, **gpad)
    ttk.Label(frm_status, text=app.tr("status.label.count")).grid(row=0, column=2, padx=(16, 4), pady=1, sticky="w")
    ttk.Label(frm_status, textvariable=app.var_count).grid(row=0, column=3, **gpad)
    ttk.Label(frm_status, text=app.tr("status.label.debug")).grid(row=0, column=4, padx=(16, 4), pady=1, sticky="w")
    ttk.Label(frm_status, textvariable=app.var_debug).grid(row=0, column=5, **gpad)
    ttk.Label(frm_status, text=app.tr("status.label.window")).grid(row=1, column=0, **gpad)
    app.lbl_window = ttk.Label(frm_status, textvariable=app.var_window)
    app.lbl_window.grid(row=1, column=1, columnspan=5, **gpad)


def build_control_panel(app, parent, pad):
    frm_btn = ttk.Frame(parent)
    frm_btn.pack(fill="x", **pad)

    row1 = ttk.Frame(frm_btn)
    row1.pack(fill="x")
    app.btn_start = ttk.Button(row1, text=app.tr("button.start"), command=app._on_start, width=12)
    app.btn_start.pack(side="left", padx=2, pady=1)
    app.btn_stop = ttk.Button(
        row1, text=app.tr("button.stop"), command=app._on_stop, width=12, state="disabled"
    )
    app.btn_stop.pack(side="left", padx=2, pady=1)
    app.btn_debug = ttk.Button(row1, text=app.tr("button.debug"), command=app._on_toggle_debug, width=10)
    app.btn_debug.pack(side="left", padx=2, pady=1)
    app.btn_connect = ttk.Button(row1, text=app.tr("button.connect"), command=app._on_connect, width=12)
    app.btn_connect.pack(side="left", padx=2, pady=1)

    row2 = ttk.Frame(frm_btn)
    row2.pack(fill="x")
    app.btn_screenshot = ttk.Button(row2, text=app.tr("button.screenshot"), command=app._on_screenshot, width=8)
    app.btn_screenshot.pack(side="left", padx=2, pady=1)
    app.btn_clearlog = ttk.Button(row2, text=app.tr("button.clearLog"), command=app._on_clear_log, width=8)
    app.btn_clearlog.pack(side="left", padx=2, pady=1)
    app.btn_whitelist = ttk.Button(row2, text=app.tr("button.whitelist"), command=app._on_whitelist, width=8)
    app.btn_whitelist.pack(side="left", padx=2, pady=1)
    app.btn_stats = ttk.Button(row2, text=app.tr("button.stats"), command=app._on_stats, width=8)
    app.btn_stats.pack(side="left", padx=2, pady=1)
    app.btn_roi = ttk.Button(row2, text=app.tr("button.selectRoi"), command=app._on_select_roi, width=10)
    app.btn_roi.pack(side="left", padx=2, pady=1)
    app.btn_clear_roi = ttk.Button(row2, text=app.tr("button.clearRoi"), command=app._on_clear_roi, width=10)
    app.btn_clear_roi.pack(side="left", padx=2, pady=1)


def build_toggle_panel(app, parent, pad):
    frm_toggles = ttk.Frame(parent)
    frm_toggles.pack(fill="x", **pad)

    # 第一行：基础开关
    row1 = ttk.Frame(frm_toggles)
    row1.pack(fill="x", pady=1)

    app.var_topmost = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        row1, text=app.tr("toggle.topmost"), variable=app.var_topmost, command=app._on_topmost
    ).pack(side="left", padx=4)

    app.var_show_debug = tk.BooleanVar(value=config.SHOW_DEBUG)
    chk_debug = ttk.Checkbutton(
        row1, text=app.tr("toggle.debugWindow"),
        variable=app.var_show_debug, command=app._on_debug_toggle
    )
    chk_debug.pack(side="left", padx=4)
    app._create_tooltip(chk_debug, app.tr("tooltip.debugWindow"))

    app.var_skip_success = tk.BooleanVar(
        value=getattr(config, "SKIP_SUCCESS_CHECK", False)
    )
    chk_skip = ttk.Checkbutton(
        row1, text=app.tr("toggle.skipSuccess"),
        variable=app.var_skip_success, command=app._on_skip_success_toggle
    )
    chk_skip.pack(side="left", padx=4)
    app._create_tooltip(chk_skip, app.tr("tooltip.skipSuccess"))

    app.var_sync_pd_mode = tk.BooleanVar(value=getattr(config, "SYNC_PD_MODE", False))
    chk_sync_pd = ttk.Checkbutton(
        row1,
        text=app.tr("toggle.syncPdMode"),
        variable=app.var_sync_pd_mode,
        command=app._on_sync_pd_mode_toggle,
    )
    chk_sync_pd.pack(side="left", padx=4)
    app._create_tooltip(chk_sync_pd, app.tr("tooltip.syncPdMode"))

    # 第二行：区域与语言
    row2 = ttk.Frame(frm_toggles)
    row2.pack(fill="x", pady=1)

    ttk.Label(row2, text=app.tr("toggle.area")).pack(side="left", padx=(4, 2))
    app.var_roi = tk.StringVar(value=app.tr("toggle.roiUnset"))
    app.lbl_roi = ttk.Label(row2, textvariable=app.var_roi, foreground="gray")
    app.lbl_roi.pack(side="left")

    ttk.Label(row2, text=app.tr("toggle.language")).pack(side="left", padx=(16, 2))
    cmb_lang = ttk.Combobox(
        row2,
        textvariable=app.var_language,
        values=list(app._language_label_to_code.keys()),
        state="readonly",
        width=12,
    )
    cmb_lang.pack(side="left", padx=2)
    cmb_lang.bind("<<ComboboxSelected>>", app._on_language_change)


def build_yolo_panel(app, parent, pad):
    frm_yolo = ttk.LabelFrame(parent, text=app.tr("yolo.frame"))
    frm_yolo.pack(fill="x", **pad)

    ttk.Label(frm_yolo, text=app.tr("yolo.enabled")).pack(side="left", padx=4)
    app.var_yolo_collect = tk.BooleanVar(value=config.YOLO_COLLECT)
    ttk.Checkbutton(
        frm_yolo, text=app.tr("yolo.collect"),
        variable=app.var_yolo_collect, command=app._on_yolo_collect_toggle
    ).pack(side="left", padx=4)

    ttk.Label(frm_yolo, text=app.tr("yolo.device")).pack(side="left", padx=(8, 2))
    app.var_yolo_device = tk.StringVar(value=config.normalize_yolo_device(config.YOLO_DEVICE))
    cmb_dev = ttk.Combobox(
        frm_yolo, textvariable=app.var_yolo_device,
        values=["auto", "cpu", "cuda", "ncnn"], state="readonly", width=5
    )
    cmb_dev.pack(side="left", padx=2)
    cmb_dev.bind("<<ComboboxSelected>>", app._on_yolo_device_change)
    lbl_yolo_device_help = tk.Label(
        frm_yolo,
        text="?",
        fg="gray",
        cursor="question_arrow",
        padx=2,
    )
    lbl_yolo_device_help.pack(side="left", padx=(0, 4))
    app._create_tooltip(lbl_yolo_device_help, app.tr("tooltip.yoloDevice"))
    lbl_yolo_tutorial = tk.Label(
        frm_yolo,
        text="教程",
        fg="gray",
        cursor="question_arrow",
        padx=2,
    )
    lbl_yolo_tutorial.pack(side="left", padx=(0, 6))
    app._create_tooltip(lbl_yolo_tutorial, app.tr("tooltip.yoloDeviceTutorial"))

    app.var_full_rate_wait_hook = tk.BooleanVar(value=config.FULL_RATE_WAIT_HOOK)
    ttk.Checkbutton(
        frm_yolo,
        text=app.tr("yolo.fullRateWaitHook"),
        variable=app.var_full_rate_wait_hook,
        command=app._on_full_rate_wait_hook_toggle,
    ).pack(side="left", padx=(8, 4))

    app.var_yolo_status = tk.StringVar(value="")
    app._update_yolo_status()
    ttk.Label(frm_yolo, textvariable=app.var_yolo_status, foreground="gray").pack(
        side="left", padx=8
    )
