"""
Fish Trainer GUI
================
提供采集、标注和导出已标注图片+标签 zip 的桌面界面。
"""

import argparse
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fish_trainer import collect, label
from fish_trainer.exporter import build_export_name, export_labeled_dataset, get_dataset_stats
from fish_trainer.paths import APP_ROOT, BASE, UNLABELED, ensure_dataset_dirs


def build_parser():
    parser = argparse.ArgumentParser(description="Fish Trainer GUI")
    parser.add_argument("--tool", choices=("collect", "label"), help="内部子工具入口")
    parser.add_argument("--fps", type=float, default=2.0, help="采集 FPS")
    parser.add_argument("--roi", action="store_true", help="采集时只截取 ROI")
    parser.add_argument("--max", type=int, default=0, help="采集最大截图数")
    parser.add_argument("--split", type=float, default=0.2, help="标注时验证集比例")
    parser.add_argument("--relabel", action="store_true", help="重新标注已有数据")
    return parser


def dispatch_tool(args):
    if args.tool == "collect":
        collect.main([
            "--fps", str(args.fps),
            "--max", str(args.max),
            * (["--roi"] if args.roi else []),
        ])
        return True
    if args.tool == "label":
        label_args = ["--split", str(args.split)]
        if args.relabel:
            label_args.append("--relabel")
        label.main(label_args)
        return True
    return False


class FishTrainerGUI:
    def __init__(self, root):
        self.root = root
        self.collect_proc = None
        self.label_proc = None

        ensure_dataset_dirs()
        self.root.title("Fish Trainer")
        self.root.geometry("760x620")
        self.root.minsize(720, 560)

        self.fps_var = tk.StringVar(value="2.0")
        self.max_var = tk.StringVar(value="0")
        self.split_var = tk.StringVar(value="0.2")
        self.roi_var = tk.BooleanVar(value=True)

        self.unlabeled_var = tk.StringVar()
        self.train_var = tk.StringVar()
        self.val_var = tk.StringVar()
        self.pairs_var = tk.StringVar()

        self._build_ui()
        self.refresh_stats()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(3, weight=1)

        top = ttk.LabelFrame(main, text="数据目录", padding=10)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="工作目录").grid(row=0, column=0, sticky="w")
        ttk.Label(top, text=APP_ROOT).grid(row=0, column=1, sticky="w")
        ttk.Label(top, text="数据目录").grid(row=1, column=0, sticky="w")
        ttk.Label(top, text=BASE).grid(row=1, column=1, sticky="w")

        stats = ttk.LabelFrame(main, text="当前统计", padding=10)
        stats.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        for idx in range(4):
            stats.columnconfigure(idx, weight=1)

        ttk.Label(stats, text="未标注").grid(row=0, column=0, sticky="w")
        ttk.Label(stats, textvariable=self.unlabeled_var).grid(row=0, column=1, sticky="w")
        ttk.Label(stats, text="Train").grid(row=0, column=2, sticky="w")
        ttk.Label(stats, textvariable=self.train_var).grid(row=0, column=3, sticky="w")
        ttk.Label(stats, text="Val").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(stats, textvariable=self.val_var).grid(row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(stats, text="可导出配对").grid(row=1, column=2, sticky="w", pady=(6, 0))
        ttk.Label(stats, textvariable=self.pairs_var).grid(row=1, column=3, sticky="w", pady=(6, 0))

        controls = ttk.LabelFrame(main, text="操作", padding=10)
        controls.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        for idx in range(6):
            controls.columnconfigure(idx, weight=1)

        ttk.Label(controls, text="采集 FPS").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.fps_var, width=10).grid(row=0, column=1, sticky="w")
        ttk.Label(controls, text="最大张数").grid(row=0, column=2, sticky="w")
        ttk.Entry(controls, textvariable=self.max_var, width=10).grid(row=0, column=3, sticky="w")
        ttk.Checkbutton(controls, text="仅采集已保存 ROI", variable=self.roi_var).grid(
            row=0, column=4, columnspan=2, sticky="w"
        )

        ttk.Button(controls, text="开始采集", command=self.start_collect).grid(
            row=1, column=0, sticky="ew", pady=(10, 0)
        )
        ttk.Button(controls, text="停止采集", command=self.stop_collect).grid(
            row=1, column=1, sticky="ew", pady=(10, 0)
        )

        ttk.Label(controls, text="验证集比例").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(controls, textvariable=self.split_var, width=10).grid(
            row=2, column=1, sticky="w", pady=(10, 0)
        )
        ttk.Button(controls, text="开始标注", command=self.start_label).grid(
            row=2, column=2, sticky="ew", pady=(10, 0)
        )
        ttk.Button(controls, text="补标已有数据", command=lambda: self.start_label(relabel=True)).grid(
            row=2, column=3, sticky="ew", pady=(10, 0)
        )

        ttk.Button(controls, text="导出图片+标签 zip", command=self.export_zip).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0)
        )
        ttk.Button(controls, text="打开未标注目录", command=lambda: self.open_dir(UNLABELED)).grid(
            row=3, column=2, sticky="ew", pady=(10, 0)
        )
        ttk.Button(controls, text="打开已标注目录", command=lambda: self.open_dir(BASE)).grid(
            row=3, column=3, sticky="ew", pady=(10, 0)
        )
        ttk.Button(controls, text="刷新统计", command=self.refresh_stats).grid(
            row=3, column=4, sticky="ew", pady=(10, 0)
        )

        tips = ttk.LabelFrame(main, text="说明", padding=10)
        tips.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        tips.columnconfigure(0, weight=1)
        tips.rowconfigure(1, weight=1)

        ttk.Label(
            tips,
            text=(
                "1. 先点击“开始采集”截训练图。\n"
                "2. 再点击“开始标注”进入 OpenCV 标注窗口。\n"
                "3. 导出按钮会把已标注图片和对应 .txt 标签压成一个 zip，直接发给别人即可。"
            ),
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        self.log_box = scrolledtext.ScrolledText(tips, height=18, state="disabled", wrap="word")
        self.log_box.grid(row=1, column=0, sticky="nsew", pady=(10, 0))

    def log(self, message):
        stamp = time.strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{stamp}] {message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def refresh_stats(self):
        stats = get_dataset_stats()
        self.unlabeled_var.set(f"{stats['unlabeled_images']} 张")
        self.train_var.set(f"{stats['train_images']} 图 / {stats['train_labels']} 标")
        self.val_var.set(f"{stats['val_images']} 图 / {stats['val_labels']} 标")
        self.pairs_var.set(f"{stats['labeled_pairs']} 对")

    def build_runner_command(self, tool_name, extra_args):
        if getattr(sys, "frozen", False):
            return [sys.executable, "--tool", tool_name, *extra_args]
        return [sys.executable, os.path.abspath(__file__), "--tool", tool_name, *extra_args]

    def start_process(self, cmd, process_kind):
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        if process_kind == "collect":
            self.collect_proc = proc
        else:
            self.label_proc = proc

        self.log(f"启动 {process_kind}: {' '.join(cmd)}")
        threading.Thread(
            target=self._stream_process_output,
            args=(proc, process_kind),
            daemon=True,
        ).start()
        return proc

    def _stream_process_output(self, proc, process_kind):
        assert proc.stdout is not None
        for line in proc.stdout:
            clean = line.rstrip()
            if clean:
                self.root.after(0, self.log, f"{process_kind}> {clean}")
        code = proc.wait()
        self.root.after(0, self._on_process_done, process_kind, code)

    def _on_process_done(self, process_kind, code):
        if process_kind == "collect":
            self.collect_proc = None
        else:
            self.label_proc = None
        self.log(f"{process_kind} 已结束，退出码: {code}")
        self.refresh_stats()

    def start_collect(self):
        if self.collect_proc and self.collect_proc.poll() is None:
            messagebox.showinfo("提示", "采集已经在运行中")
            return

        try:
            fps = float(self.fps_var.get().strip())
            max_count = int(self.max_var.get().strip())
        except ValueError:
            messagebox.showerror("参数错误", "请填写正确的采集 FPS 和最大张数")
            return

        cmd = self.build_runner_command(
            "collect",
            [
                "--fps", str(fps),
                "--max", str(max_count),
                * (["--roi"] if self.roi_var.get() else []),
            ],
        )
        self.start_process(cmd, "collect")

    def stop_collect(self):
        if not self.collect_proc or self.collect_proc.poll() is not None:
            messagebox.showinfo("提示", "当前没有运行中的采集任务")
            return
        self.collect_proc.terminate()
        self.log("已请求停止采集")

    def start_label(self, relabel=False):
        if self.label_proc and self.label_proc.poll() is None:
            messagebox.showinfo("提示", "标注工具已经在运行中")
            return

        try:
            split = float(self.split_var.get().strip())
        except ValueError:
            messagebox.showerror("参数错误", "请填写正确的验证集比例")
            return

        cmd = self.build_runner_command(
            "label",
            [
                "--split", str(split),
                * (["--relabel"] if relabel else []),
            ],
        )
        self.start_process(cmd, "label")

    def export_zip(self):
        self.refresh_stats()
        default_name = build_export_name()
        zip_path = filedialog.asksaveasfilename(
            title="导出已标注图片和标签",
            defaultextension=".zip",
            initialdir=APP_ROOT,
            initialfile=default_name,
            filetypes=[("ZIP 文件", "*.zip")],
        )
        if not zip_path:
            return

        try:
            count = export_labeled_dataset(zip_path)
        except ValueError as exc:
            messagebox.showinfo("没有可导出的数据", str(exc))
            return
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            return

        self.log(f"已导出 {count} 对图片+标签 -> {zip_path}")
        messagebox.showinfo("导出完成", f"已导出 {count} 对图片和标签:\n{zip_path}")

    def open_dir(self, path):
        os.makedirs(path, exist_ok=True)
        os.startfile(path)

    def on_close(self):
        running = []
        if self.collect_proc and self.collect_proc.poll() is None:
            running.append("采集")
        if self.label_proc and self.label_proc.poll() is None:
            running.append("标注")

        if running:
            if not messagebox.askyesno("确认退出", f"当前仍有任务在运行: {', '.join(running)}\n确定直接退出吗？"):
                return
            for proc in (self.collect_proc, self.label_proc):
                if proc and proc.poll() is None:
                    proc.terminate()

        self.root.destroy()


def launch_gui():
    root = tk.Tk()
    style = ttk.Style()
    if "vista" in style.theme_names():
        style.theme_use("vista")
    FishTrainerGUI(root)
    root.mainloop()


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if dispatch_tool(args):
        return
    launch_gui()


if __name__ == "__main__":
    main()
