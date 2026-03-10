"""
GUI 面板构建辅助
================
抽取 FishingApp 中的基础面板构建代码。
"""

import tkinter as tk
from tkinter import ttk

import config


def build_status_panel(app, parent, pad):
    frm_status = ttk.LabelFrame(parent, text=" 状态 ")
    frm_status.pack(fill="x", **pad)
    gpad = {"padx": 6, "pady": 1, "sticky": "w"}

    app.var_state = tk.StringVar(value="就绪")
    app.var_window = tk.StringVar(value="未连接")
    app.var_count = tk.StringVar(value="0")
    app.var_debug = tk.StringVar(value="关闭")

    ttk.Label(frm_status, text="状态:").grid(row=0, column=0, **gpad)
    app.lbl_state = ttk.Label(frm_status, textvariable=app.var_state, foreground="gray")
    app.lbl_state.grid(row=0, column=1, **gpad)
    ttk.Label(frm_status, text="次数:").grid(row=0, column=2, padx=(16, 4), pady=1, sticky="w")
    ttk.Label(frm_status, textvariable=app.var_count).grid(row=0, column=3, **gpad)
    ttk.Label(frm_status, text="调试:").grid(row=0, column=4, padx=(16, 4), pady=1, sticky="w")
    ttk.Label(frm_status, textvariable=app.var_debug).grid(row=0, column=5, **gpad)
    ttk.Label(frm_status, text="窗口:").grid(row=1, column=0, **gpad)
    app.lbl_window = ttk.Label(frm_status, textvariable=app.var_window)
    app.lbl_window.grid(row=1, column=1, columnspan=5, **gpad)


def build_control_panel(app, parent, pad):
    frm_btn = ttk.Frame(parent)
    frm_btn.pack(fill="x", **pad)

    row1 = ttk.Frame(frm_btn)
    row1.pack(fill="x")
    app.btn_start = ttk.Button(row1, text="▶ 开始(F9)", command=app._on_start, width=12)
    app.btn_start.pack(side="left", padx=2, pady=1)
    app.btn_stop = ttk.Button(
        row1, text="■ 停止(F10)", command=app._on_stop, width=12, state="disabled"
    )
    app.btn_stop.pack(side="left", padx=2, pady=1)
    app.btn_debug = ttk.Button(row1, text="调试(F11)", command=app._on_toggle_debug, width=10)
    app.btn_debug.pack(side="left", padx=2, pady=1)
    app.btn_connect = ttk.Button(row1, text="🔗 连接窗口", command=app._on_connect, width=12)
    app.btn_connect.pack(side="left", padx=2, pady=1)

    row2 = ttk.Frame(frm_btn)
    row2.pack(fill="x")
    app.btn_screenshot = ttk.Button(row2, text="📸 截图", command=app._on_screenshot, width=8)
    app.btn_screenshot.pack(side="left", padx=2, pady=1)
    app.btn_clearlog = ttk.Button(row2, text="🗑 清日志", command=app._on_clear_log, width=8)
    app.btn_clearlog.pack(side="left", padx=2, pady=1)
    app.btn_whitelist = ttk.Button(row2, text="🐟 白名单", command=app._on_whitelist, width=8)
    app.btn_whitelist.pack(side="left", padx=2, pady=1)
    app.btn_roi = ttk.Button(row2, text="📐 框选区域", command=app._on_select_roi, width=10)
    app.btn_roi.pack(side="left", padx=2, pady=1)
    app.btn_clear_roi = ttk.Button(row2, text="✕ 清除区域", command=app._on_clear_roi, width=10)
    app.btn_clear_roi.pack(side="left", padx=2, pady=1)


def build_toggle_panel(app, parent, pad):
    frm_toggles = ttk.Frame(parent)
    frm_toggles.pack(fill="x", **pad)

    app.var_topmost = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        frm_toggles, text="窗口置顶", variable=app.var_topmost, command=app._on_topmost
    ).pack(side="left", padx=4)

    app.var_show_debug = tk.BooleanVar(value=config.SHOW_DEBUG)
    ttk.Checkbutton(
        frm_toggles, text="Debug窗口",
        variable=app.var_show_debug, command=app._on_debug_toggle
    ).pack(side="left", padx=4)

    app.var_skip_success = tk.BooleanVar(
        value=getattr(config, "SKIP_SUCCESS_CHECK", False)
    )
    chk_skip = ttk.Checkbutton(
        frm_toggles, text="跳过成功检查",
        variable=app.var_skip_success, command=app._on_skip_success_toggle
    )
    chk_skip.pack(side="left", padx=4)
    app._create_tooltip(
        chk_skip,
        "启用后不再检测成功阈值，无论成功失败都点击两次收杆。\n"
        "因为游戏成功需要点一次才能收杆，失败则不用点。\n"
        "很多人反馈在成功判定处卡住，开启此选项可避免。",
    )

    app.var_sync_pd_mode = tk.BooleanVar(value=getattr(config, "SYNC_PD_MODE", False))
    chk_sync_pd = ttk.Checkbutton(
        frm_toggles,
        text="旧版模式（使用旧版参数）",
        variable=app.var_sync_pd_mode,
        command=app._on_sync_pd_mode_toggle,
    )
    chk_sync_pd.pack(side="left", padx=4)
    app._create_tooltip(
        chk_sync_pd,
        "开启: 小游戏 PD 控制走旧版同步闭环\n"
        "即截图->检测->控制同线程执行，更接近旧手感。\n"
        "关闭: 使用当前异步截图/检测流水线。\n"
        "仅 PD 控制器生效，录制/行为克隆不受影响。",
    )

    ttk.Label(frm_toggles, text="区域:").pack(side="left", padx=(10, 2))
    app.var_roi = tk.StringVar(value="未设置 (全屏搜索)")
    app.lbl_roi = ttk.Label(frm_toggles, textvariable=app.var_roi, foreground="gray")
    app.lbl_roi.pack(side="left")


def build_yolo_panel(app, parent, pad):
    frm_yolo = ttk.LabelFrame(parent, text=" YOLO 目标检测 ")
    frm_yolo.pack(fill="x", **pad)

    config.USE_YOLO = True
    ttk.Label(frm_yolo, text="YOLO 已启用").pack(side="left", padx=4)
    app.var_yolo_collect = tk.BooleanVar(value=config.YOLO_COLLECT)
    ttk.Checkbutton(
        frm_yolo, text="采集数据",
        variable=app.var_yolo_collect, command=app._on_yolo_collect_toggle
    ).pack(side="left", padx=4)

    ttk.Label(frm_yolo, text="设备:").pack(side="left", padx=(8, 2))
    app.var_yolo_device = tk.StringVar(value=config.YOLO_DEVICE)
    cmb_dev = ttk.Combobox(
        frm_yolo, textvariable=app.var_yolo_device,
        values=["auto", "cpu", "gpu"], state="readonly", width=5
    )
    cmb_dev.pack(side="left", padx=2)
    cmb_dev.bind("<<ComboboxSelected>>", app._on_yolo_device_change)

    app.var_yolo_status = tk.StringVar(value="")
    app._update_yolo_status()
    ttk.Label(frm_yolo, textvariable=app.var_yolo_status, foreground="gray").pack(
        side="left", padx=8
    )
