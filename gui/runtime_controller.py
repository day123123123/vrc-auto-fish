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
from utils.logger import log


class AppRuntimeController:
    """封装 FishingApp 的运行时动作。"""

    FISH_NAMES = [
        ("fish_black", "黑鱼"),
        ("fish_white", "白鱼"),
        ("fish_copper", "铜鱼"),
        ("fish_green", "绿鱼"),
        ("fish_blue", "蓝鱼"),
        ("fish_purple", "紫鱼"),
        ("fish_pink", "粉鱼"),
        ("fish_red", "红鱼"),
        ("fish_rainbow", "彩鱼"),
    ]

    def __init__(self, app):
        self.app = app

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
            self.app._log_msg("[错误] 程序所在路径包含中文或特殊字符，会导致图片/模型加载失败！")
            self.app._log_msg(f"  当前路径: {config.BASE_DIR}")
            self.app._log_msg("  请将程序移动到纯英文路径下再运行，例如: D:\\fish")
            return
        if not self.app.bot.window.is_valid():
            if not self.app.bot.window.find():
                self.app._log_msg("[错误] 未找到 VRChat 窗口！请确保游戏正在运行。")
                return

        self.app.var_window.set(
            f"{self.app.bot.window.title} (HWND={self.app.bot.window.hwnd})"
        )
        self.app._apply_params()
        self.app.bot.running = True
        self.app.bot.state = "运行中"
        if self.app.bot_thread is None or not self.app.bot_thread.is_alive():
            self.app.bot_thread = threading.Thread(
                target=self.app.bot.run, daemon=True
            )
            self.app.bot_thread.start()

        self.app.btn_start.config(state="disabled")
        self.app.btn_stop.config(state="normal")
        self.app.btn_roi.config(state="disabled")
        self.app.btn_clear_roi.config(state="disabled")
        self.app._log_msg("[系统] ▶ 开始自动钓鱼")

    def on_stop(self):
        self.app.bot.running = False
        self.app.bot._force_minigame = False
        self.app.bot.input.safe_release()
        self.app.bot.shutdown_debug_overlay()
        self.app.btn_start.config(state="normal")
        self.app.btn_stop.config(state="disabled")
        self.app.btn_roi.config(state="normal")
        self.app.btn_clear_roi.config(state="normal")
        self.app._log_msg("[系统] ■ 已停止")
        self.save_log_async()

    def on_toggle_debug(self):
        self.app.bot.debug_mode = not self.app.bot.debug_mode
        tag = "开启" if self.app.bot.debug_mode else "关闭"
        self.app.var_debug.set(tag)
        self.app._log_msg(f"[系统] 调试模式: {tag}")
        if self.app.bot.debug_mode:
            self.app._log_msg("[提示] 调试截图将保存到 debug/ 目录，检测器将输出置信度")

    def on_connect(self):
        if self.app.bot.window.find():
            self.app.var_window.set(
                f"{self.app.bot.window.title} (HWND={self.app.bot.window.hwnd})"
            )
            self.app.bot.screen.reset_capture_method()
            self.app._log_msg(f"[系统] 已连接: {self.app.bot.window.title}")
            return
        self.app.var_window.set("未找到")
        self.app._log_msg("[错误] 未找到 VRChat 窗口")

    def screen_capture_safe(self):
        try:
            return self.app.bot.screen.grab_window(self.app.bot.window)
        except Exception as e:
            self.app._log_msg(f"[错误] 截图异常: {e}")
            return None, None

    def on_screenshot(self):
        if not self.app.bot.window.is_valid():
            if not self.app.bot.window.find():
                self.app._log_msg("[错误] 无法截图: 未连接 VRChat 窗口")
                return
        img, region = self.screen_capture_safe()
        if img is None:
            self.app._log_msg("[错误] 截图失败")
            return
        self.app.bot.screen.save_debug(img, "manual_screenshot")
        h, w = img.shape[:2]
        self.app._log_msg(f"[截图] 已保存 ({w}×{h}) → debug/manual_screenshot.png")
        if region:
            self.app._log_msg(
                f"       窗口区域: x={region[0]} y={region[1]} w={region[2]} h={region[3]}"
            )

    def on_clear_log(self):
        self.app.txt_log.config(state="normal")
        self.app.txt_log.delete("1.0", "end")
        self.app.txt_log.config(state="disabled")

    def on_whitelist(self):
        win = tk.Toplevel(self.app.root)
        win.title("钓鱼白名单")
        win.resizable(False, False)
        win.transient(self.app.root)
        win.grab_set()
        ttk.Label(win, text="勾选要钓的鱼:").pack(pady=(10, 5))

        wl = config.FISH_WHITELIST
        chk_vars = {}
        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        for col in range(2):
            body.columnconfigure(col, weight=1)

        for i, (key, name) in enumerate(self.FISH_NAMES):
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
            enabled = [n for (k, n) in self.FISH_NAMES if chk_vars[k].get()]
            self.app._log_msg(f"[白名单] 已更新: {', '.join(enabled)}")
            win.destroy()

        ttk.Button(win, text="确定", command=apply_changes).pack(pady=10)
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
                self.app._log_msg(f"[YOLO] 预加载失败: {e}")

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

        parts = ["模型 ✓" if model_ok else "模型 ✗", f"训练:{n_train}", f"未标:{n_unlabeled}"]
        self.app.var_yolo_status.set(" | ".join(parts))

    def on_select_roi(self):
        if not self.app.bot.window.is_valid():
            if not self.app.bot.window.find():
                self.app._log_msg("[错误] 请先连接 VRChat 窗口")
                return
        img, _ = self.screen_capture_safe()
        if img is None:
            self.app._log_msg("[错误] 截图失败, 无法框选")
            return

        self.app._log_msg(
            "[框选] 请在弹出窗口中用鼠标框选钓鱼UI区域, 按回车确认, 按ESC取消"
        )

        def select_worker(snap):
            win_name = "Select Fishing ROI - Enter=OK / Esc=Cancel"
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
                self.app._log_msg(f"[框选] ✓ 检测区域已设置: X={x} Y={y} {w_r}x{h_r}")
            else:
                self.app._log_msg("[框选] 已取消 (区域太小或按了ESC)")

        threading.Thread(target=select_worker, args=(img,), daemon=True, name="ROISelect").start()

    def on_clear_roi(self):
        config.DETECT_ROI = None
        self.app._save_settings()
        self.app.var_roi.set("未设置 (全屏搜索)")
        self.app.lbl_roi.config(foreground="gray")
        self.app._log_msg("[框选] 已清除检测区域, 将使用全屏搜索")

    def poll(self):
        try:
            for _ in range(20):
                msg = log.log_queue.get_nowait()
                self.app._append_log(msg)
        except Exception:
            pass

        self.app.var_state.set(self.app.bot.state)
        self.app.var_count.set(str(self.app.bot.fish_count))
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
        self.app._log_msg(f"[系统] 日志已保存 → {path}")

    def save_log_async(self):
        path = os.path.join(config.DEBUG_DIR, "last_run.log")

        def worker():
            log.save(path)
            try:
                self.app.root.after(0, lambda: self.app._log_msg(f"[系统] 日志已保存 → {path}"))
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()
