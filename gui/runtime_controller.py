"""
GUI 运行时动作
==============
集中处理开始/停止/截图/ROI/轮询等运行时交互。
"""

import os
import threading
import tkinter as tk
from tkinter import ttk

import cv2

import config
from utils.i18n import fish_name, t
from utils.logger import log


class AppRuntimeController:
    """封装 FishingApp 的运行时动作。"""

    FISH_KEYS = [
        "fish_generic",
        "fish_black",
        "fish_white",
        "fish_copper",
        "fish_green",
        "fish_blue",
        "fish_purple",
        "fish_golden",
        "fish_pink",
        "fish_red",
        "fish_rainbow",
    ]

    def __init__(self, app):
        self.app = app

    def tr(self, key: str, default: str | None = None, **kwargs):
        return t(key, default=default, **kwargs)

    def _fish_pairs(self):
        return [(key, fish_name(key)) for key in self.FISH_KEYS]

    @staticmethod
    def has_non_ascii(path: str) -> bool:
        try:
            path.encode("ascii")
            return False
        except UnicodeEncodeError:
            return True

    def on_start(self):
        if self.app.bot.running:
            return
        if self.has_non_ascii(config.BASE_DIR):
            self.app._log_t("runtime.pathNonAscii")
            self.app._log_t("runtime.currentPath", path=config.BASE_DIR)
            self.app._log_t("runtime.moveToAsciiPath")
            return
        if not self.app.bot.window.is_valid():
            if not self.app.bot.window.find():
                self.app._log_t("runtime.vrchatWindowMissing")
                return

        self.app.var_window.set(
            f"{self.app.bot.window.title} (HWND={self.app.bot.window.hwnd})"
        )
        self.app._apply_params()
        self.app.bot.running = True
        self.app.bot.state = "status.running"
        if self.app.bot_thread is None or not self.app.bot_thread.is_alive():
            self.app.bot_thread = threading.Thread(
                target=self.app.bot.run, daemon=True
            )
            self.app.bot_thread.start()

        self.app.btn_start.config(state="disabled")
        self.app.btn_stop.config(state="normal")
        self.app.btn_roi.config(state="disabled")
        self.app.btn_clear_roi.config(state="disabled")
        self.app._log_t("runtime.startFishing")

    def on_stop(self):
        self.app.bot.running = False
        self.app.bot._force_minigame = False
        self.app.bot.input.safe_release()
        self.app.bot.shutdown_debug_overlay()
        self.app.btn_start.config(state="normal")
        self.app.btn_stop.config(state="disabled")
        self.app.btn_roi.config(state="normal")
        self.app.btn_clear_roi.config(state="normal")
        self.app._log_t("runtime.stopFishing")
        self.save_log_async()

    def on_toggle_debug(self):
        self.app.bot.debug_mode = not self.app.bot.debug_mode
        tag = self.tr("status.on") if self.app.bot.debug_mode else self.tr("status.off")
        self.app.var_debug.set(tag)
        self.app._log_t("runtime.debugModeChanged", state=tag)
        if self.app.bot.debug_mode:
            self.app._log_t("runtime.debugHint")

    def on_connect(self):
        if self.app.bot.window.find():
            self.app.var_window.set(
                f"{self.app.bot.window.title} (HWND={self.app.bot.window.hwnd})"
            )
            self.app.bot.screen.reset_capture_method()
            self.app._log_t("runtime.connected", title=self.app.bot.window.title)
            return
        self.app.var_window.set(self.tr("status.notFound"))
        self.app._log_t("runtime.windowNotFound")

    def screen_capture_safe(self):
        try:
            return self.app.bot.screen.grab_window(self.app.bot.window)
        except Exception as e:
            self.app._log_t("runtime.screenshotException", error=e)
            return None, None

    def on_screenshot(self):
        if not self.app.bot.window.is_valid():
            if not self.app.bot.window.find():
                self.app._log_t("runtime.screenshotWindowMissing")
                return
        img, region = self.screen_capture_safe()
        if img is None:
            self.app._log_t("runtime.screenshotFailed")
            return
        self.app.bot.screen.save_debug(img, "manual_screenshot")
        h, w = img.shape[:2]
        self.app._log_t("runtime.screenshotSaved", width=w, height=h)
        if region:
            self.app._log_t(
                "runtime.windowRegion",
                x=region[0], y=region[1], w=region[2], h=region[3]
            )

    def on_clear_log(self):
        self.app.txt_log.config(state="normal")
        self.app.txt_log.delete("1.0", "end")
        self.app.txt_log.config(state="disabled")

    def on_whitelist(self):
        win = tk.Toplevel(self.app.root)
        win.title(self.tr("runtime.whitelistTitle"))
        win.resizable(False, False)
        win.transient(self.app.root)
        win.grab_set()
        ttk.Label(win, text=self.tr("runtime.whitelistPrompt")).pack(pady=(10, 5))

        wl = config.FISH_WHITELIST
        chk_vars = {}
        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        for col in range(2):
            body.columnconfigure(col, weight=1)

        for i, (key, name) in enumerate(self._fish_pairs()):
            var = tk.BooleanVar(value=wl.get(key, True))
            chk_vars[key] = var
            row = i // 2
            col = i % 2
            ttk.Checkbutton(body, text=name, variable=var).grid(
                row=row, column=col, sticky="w", padx=12, pady=4
            )

        def apply_changes():
            for key, var in chk_vars.items():
                config.FISH_WHITELIST[key] = var.get()
            self.app._save_settings()
            enabled = [n for (k, n) in self._fish_pairs() if chk_vars[k].get()]
            self.app._log_t("runtime.whitelistUpdated", names=", ".join(enabled))
            win.destroy()

        ttk.Button(win, text=self.tr("runtime.confirm"), command=apply_changes).pack(pady=10)
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

    def on_topmost(self):
        topmost = self.app.var_topmost.get()
        self.app.root.wm_attributes("-topmost", 1 if topmost else 0)
        if not topmost:
            self.app.root.lift()
            self.app.root.focus_force()

    def preload_yolo(self):
        def load():
            try:
                from core.bot import _get_yolo_detector
                self.app.bot.yolo = _get_yolo_detector()
            except Exception as e:
                self.app._log_t("runtime.yoloPreloadFailed", error=e)

        threading.Thread(target=load, daemon=True).start()

    def update_yolo_status(self):
        """更新 YOLO 状态显示。"""
        model_ok = os.path.exists(config.YOLO_MODEL)
        unlabeled = os.path.join(
            config.BASE_DIR, "yolo", "dataset", "images", "unlabeled")
        train = os.path.join(
            config.BASE_DIR, "yolo", "dataset", "images", "train")
        n_unlabeled = len([
            f for f in os.listdir(unlabeled)
            if f.endswith((".png", ".jpg"))
        ]) if os.path.isdir(unlabeled) else 0
        n_train = len([
            f for f in os.listdir(train)
            if f.endswith((".png", ".jpg"))
        ]) if os.path.isdir(train) else 0

        parts = [
            self.tr("yolo.modelOk") if model_ok else self.tr("yolo.modelMissing"),
            self.tr("yolo.trainCount", count=n_train),
            self.tr("yolo.unlabeledCount", count=n_unlabeled),
        ]
        self.app.var_yolo_status.set(" | ".join(parts))

    def on_select_roi(self):
        if not self.app.bot.window.is_valid():
            if not self.app.bot.window.find():
                self.app._log_t("runtime.connectFirst")
                return
        img, _ = self.screen_capture_safe()
        if img is None:
            self.app._log_t("runtime.roiCaptureFailed")
            return

        self.app._log_t("runtime.roiPrompt")

        def select_worker(snap):
            win_name = self.tr("runtime.roiSelectWindow")
            cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
            h, w = snap.shape[:2]
            dw = min(w, 1280)
            dh = int(h * dw / w)
            cv2.resizeWindow(win_name, dw, dh)
            roi = cv2.selectROI(win_name, snap, fromCenter=False, showCrosshair=True)
            cv2.destroyAllWindows()
            x, y, w_r, h_r = [int(v) for v in roi]
            if w_r > 10 and h_r > 10:
                config.DETECT_ROI = [x, y, w_r, h_r]
                self.app._save_settings()
                self.app.root.after(0, lambda: self.app.var_roi.set(f"X={x} Y={y} {w_r}x{h_r}"))
                self.app.root.after(0, lambda: self.app.lbl_roi.config(foreground="green"))
                self.app._log_t("runtime.roiSet", x=x, y=y, w=w_r, h=h_r)
            else:
                self.app._log_t("runtime.roiCancelled")

        threading.Thread(target=select_worker, args=(img,), daemon=True, name="ROISelect").start()

    def on_clear_roi(self):
        config.DETECT_ROI = None
        self.app._save_settings()
        self.app.var_roi.set(self.tr("toggle.roiUnset"))
        self.app.lbl_roi.config(foreground="gray")
        self.app._log_t("runtime.roiCleared")

    def poll(self):
        try:
            for _ in range(20):
                msg = log.log_queue.get_nowait()
                self.app._append_log(msg)
        except Exception:
            pass

        self.app.var_state.set(self.app._translate_bot_state(self.app.bot.state))
        self.app.var_count.set(str(self.app.bot.fish_count))
        self.app.var_debug.set(
            self.tr("status.on") if self.app.bot.debug_mode else self.tr("status.off")
        )
        self.app.lbl_state.config(
            foreground="green" if self.app.bot.running else "gray"
        )

        if self.app.bot_thread and not self.app.bot_thread.is_alive() and self.app.bot.running:
            self.app.bot.running = False
            self.app.btn_start.config(state="normal")
            self.app.btn_stop.config(state="disabled")
            self.app.btn_roi.config(state="normal")
            self.app.btn_clear_roi.config(state="normal")

        self.app.root.after(100, self.poll)

    def on_close(self):
        self.app.bot.running = False
        self.app.bot._force_minigame = False
        self.app.bot.input.safe_release()
        self.app.bot.shutdown_debug_overlay()
        self.app._save_settings()
        self.save_log()
        self.app.root.destroy()

    def save_log(self):
        path = os.path.join(config.DEBUG_DIR, "last_run.log")
        log.save(path)
        self.app._log_t("runtime.logSaved", path=path)

    def save_log_async(self):
        path = os.path.join(config.DEBUG_DIR, "last_run.log")

        def worker():
            log.save(path)
            try:
                self.app.root.after(0, lambda: self.app._log_t("runtime.logSaved", path=path))
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()
