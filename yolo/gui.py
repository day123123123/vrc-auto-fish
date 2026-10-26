"""
YOLO Trainer GUI
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

import config
from yolo import collect, label
from yolo.exporter import build_export_name, export_labeled_dataset, get_dataset_stats
from yolo.paths import APP_ROOT, BASE, UNLABELED, ensure_dataset_dirs
from utils.i18n import available_languages, init_language, set_language, t, write_persisted_language


def build_parser():
    parser = argparse.ArgumentParser(description="YOLO Trainer GUI")
    parser.add_argument("--tool", choices=("collect", "label"), help="内部子工具入口")
    parser.add_argument("--base-dir", type=str, default="", help="数据目录（可选，默认使用配置路径）")
    parser.add_argument("--fps", type=float, default=2.0, help="采集 FPS")
    parser.add_argument("--roi", action="store_true", help="采集时只截取 ROI")
    parser.add_argument("--max", type=int, default=0, help="采集最大截图数")
    parser.add_argument("--split", type=float, default=0.2, help="标注时验证集比例")
    parser.add_argument("--relabel", action="store_true", help="重新标注已有数据")
    parser.add_argument("--predict-model", type=str, default="", help="自动打标模型路径")
    parser.add_argument("--auto-predict", action="store_true", help="启用自动打标")
    return parser


def dispatch_tool(args, extra_args=None):
    extra_args = extra_args or []
    if args.tool == "collect":
        collect_args = [
            "--fps", str(args.fps),
            "--max", str(args.max),
            * (["--roi"] if args.roi else []),
        ]
        if args.base_dir:
            collect_args.extend(["--base-dir", args.base_dir])
        collect_args.extend(extra_args)
        print("[DEBUG] dispatch_tool -> collect_args:", collect_args)
        collect.main(collect_args)
        return True
    if args.tool == "label":
        label_args = ["--split", str(args.split)]
        if args.relabel:
            label_args.append("--relabel")
        if args.predict_model:
            label_args.extend(["--predict-model", args.predict_model])
        if args.auto_predict:
            label_args.append("--auto-predict")
        if args.base_dir:
            label_args.extend(["--base-dir", args.base_dir])
        label_args.extend(extra_args)
        label.main(label_args)
        return True
    return False


class YoloTrainerGUI:
    def __init__(self, root):
        self.root = root
        self.collect_proc = None
        self.label_proc = None
        self._language_code_to_label = {}
        self._language_label_to_code = {}

        # 动态路径支持
        self.base_dir_var = tk.StringVar(value=BASE)

        ensure_dataset_dirs()
        init_language()
        self.lang_var = tk.StringVar(value="")
        self._update_window_title()
        self.root.geometry("760x650")
        self.root.minsize(720, 600)

        self.fps_var = tk.StringVar(value="2.0")
        self.max_var = tk.StringVar(value="0")
        self.split_var = tk.StringVar(value="0.2")
        self.roi_var = tk.BooleanVar(value=True)
        self.predict_model_var = tk.StringVar(value="")
        self.auto_predict_var = tk.BooleanVar(value=True)

        self.unlabeled_var = tk.StringVar()
        self.train_var = tk.StringVar()
        self.val_var = tk.StringVar()
        self.pairs_var = tk.StringVar()

        self._build_ui()
        self.refresh_stats()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def tr(self, key: str, default: str | None = None, **kwargs):
        return t(key, default=default, **kwargs)

    def _update_window_title(self):
        self.root.title(self.tr("trainer.windowTitle"))

    def _refresh_language_choices(self):
        choices = available_languages()
        self._language_code_to_label = {code: label for code, label in choices}
        self._language_label_to_code = {label: code for code, label in choices}
        self.lang_var.set(self._language_code_to_label.get(config.LANGUAGE, config.LANGUAGE))

    def _rebuild_ui_for_language(self):
        log_text = ""
        if hasattr(self, "log_box"):
            log_text = self.log_box.get("1.0", "end-1c")
        for child in self.root.winfo_children():
            child.destroy()
        self._update_window_title()
        self._build_ui()
        self.refresh_stats()
        if log_text:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", log_text + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

    def _on_language_change(self, _event=None):
        selected = self.lang_var.get()
        lang = self._language_label_to_code.get(selected, selected)
        set_language(lang)
        write_persisted_language(lang)
        self._rebuild_ui_for_language()
        self.log(self.tr("log.languageChanged", language=self._language_code_to_label.get(lang, lang)))

    def _choose_data_dir(self):
        path = filedialog.askdirectory(initialdir=self.base_dir_var.get())
        if path:
            self.base_dir_var.set(os.path.abspath(path))
            # 更新全局路径
            import yolo.paths
            yolo.paths.BASE = self.base_dir_var.get()
            yolo.paths.UNLABELED = os.path.join(yolo.paths.BASE, "images", "unlabeled")
            yolo.paths.TRAIN_IMG = os.path.join(yolo.paths.BASE, "images", "train")
            yolo.paths.TRAIN_LBL = os.path.join(yolo.paths.BASE, "labels", "train")
            yolo.paths.VAL_IMG = os.path.join(yolo.paths.BASE, "images", "val")
            yolo.paths.VAL_LBL = os.path.join(yolo.paths.BASE, "labels", "val")
            
            # 同样更新 exporter 里的路径（如果它们不是动态读取 paths 的话，这里 exporter 是动态导入的）
            import yolo.exporter
            yolo.exporter.TRAIN_IMG = yolo.paths.TRAIN_IMG
            yolo.exporter.TRAIN_LBL = yolo.paths.TRAIN_LBL
            yolo.exporter.VAL_IMG = yolo.paths.VAL_IMG
            yolo.exporter.VAL_LBL = yolo.paths.VAL_LBL
            yolo.exporter.UNLABELED = yolo.paths.UNLABELED

            ensure_dataset_dirs()
            self.refresh_stats()
            self.log(f"数据目录已更改为: {yolo.paths.BASE}")

    def _choose_predict_model(self):
        initial_dir = os.path.join(APP_ROOT, "yolo", "runs", "fish_detect", "weights")
        if not os.path.exists(initial_dir):
            initial_dir = APP_ROOT
        path = filedialog.askopenfilename(
            title="选择自动打标模型",
            initialdir=initial_dir,
            filetypes=[("YOLO Model", "*.pt"), ("All Files", "*.*")],
        )
        if path:
            self.predict_model_var.set(os.path.abspath(path))
            self.log(f"已选择自动打标模型: {os.path.basename(path)}")

    def _build_ui(self):
        self._refresh_language_choices()
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(3, weight=1)

        top = ttk.LabelFrame(main, text=self.tr("trainer.frame.paths"), padding=10)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=1)

        ttk.Label(top, text=self.tr("trainer.label.workdir")).grid(row=0, column=0, sticky="w")
        ttk.Label(top, text=APP_ROOT).grid(row=0, column=1, sticky="w")
        
        ttk.Label(top, text=self.tr("trainer.label.datadir")).grid(row=1, column=0, sticky="w")
        ttk.Label(top, textvariable=self.base_dir_var, wraplength=400).grid(row=1, column=1, sticky="w")
        ttk.Button(top, text=self.tr("trainer.button.chooseDataDir", default="选择目录"), command=self._choose_data_dir).grid(row=1, column=2, padx=(10, 0))

        ttk.Label(top, text=self.tr("toggle.language")).grid(row=0, column=2, sticky="e", padx=(12, 4))
        cmb_lang = ttk.Combobox(
            top,
            textvariable=self.lang_var,
            values=list(self._language_label_to_code.keys()),
            state="readonly",
            width=14,
        )
        cmb_lang.grid(row=0, column=3, sticky="w")
        cmb_lang.bind("<<ComboboxSelected>>", self._on_language_change)

        stats = ttk.LabelFrame(main, text=self.tr("trainer.frame.stats"), padding=10)
        stats.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        for idx in range(4):
            stats.columnconfigure(idx, weight=1)

        ttk.Label(stats, text=self.tr("trainer.label.unlabeled")).grid(row=0, column=0, sticky="w")
        ttk.Label(stats, textvariable=self.unlabeled_var).grid(row=0, column=1, sticky="w")
        ttk.Label(stats, text=self.tr("trainer.label.train")).grid(row=0, column=2, sticky="w")
        ttk.Label(stats, textvariable=self.train_var).grid(row=0, column=3, sticky="w")
        ttk.Label(stats, text=self.tr("trainer.label.val")).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(stats, textvariable=self.val_var).grid(row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(stats, text=self.tr("trainer.label.pairs")).grid(row=1, column=2, sticky="w", pady=(6, 0))
        ttk.Label(stats, textvariable=self.pairs_var).grid(row=1, column=3, sticky="w", pady=(6, 0))

        controls = ttk.LabelFrame(main, text=self.tr("trainer.frame.controls"), padding=10)
        controls.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        for idx in range(6):
            controls.columnconfigure(idx, weight=1)

        ttk.Label(controls, text=self.tr("trainer.label.collectFps")).grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.fps_var, width=10).grid(row=0, column=1, sticky="w")
        ttk.Label(controls, text=self.tr("trainer.label.maxCount")).grid(row=0, column=2, sticky="w")
        ttk.Entry(controls, textvariable=self.max_var, width=10).grid(row=0, column=3, sticky="w")
        ttk.Checkbutton(controls, text=self.tr("trainer.label.savedRoiOnly"), variable=self.roi_var).grid(
            row=0, column=4, columnspan=2, sticky="w"
        )

        ttk.Button(controls, text=self.tr("trainer.button.startCollect"), command=self.start_collect).grid(
            row=1, column=0, sticky="ew", pady=(10, 0)
        )
        ttk.Button(controls, text=self.tr("trainer.button.stopCollect"), command=self.stop_collect).grid(
            row=1, column=1, sticky="ew", pady=(10, 0)
        )

        ttk.Label(controls, text=self.tr("trainer.label.split")).grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(controls, textvariable=self.split_var, width=10).grid(
            row=2, column=1, sticky="w", pady=(10, 0)
        )
        ttk.Button(controls, text=self.tr("trainer.button.startLabel"), command=self.start_label).grid(
            row=2, column=2, sticky="ew", pady=(10, 0)
        )
        ttk.Button(controls, text=self.tr("trainer.button.relabel"), command=lambda: self.start_label(relabel=True)).grid(
            row=2, column=3, sticky="ew", pady=(10, 0)
        )

        ttk.Label(controls, text="自动打标模型:").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(controls, textvariable=self.predict_model_var, width=20).grid(row=3, column=1, columnspan=2, sticky="ew", pady=(10, 0), padx=(0, 5))
        ttk.Button(controls, text="选择模型", command=self._choose_predict_model).grid(row=3, column=3, sticky="ew", pady=(10, 0))
        ttk.Checkbutton(controls, text="打开图片自动预测", variable=self.auto_predict_var).grid(row=3, column=4, columnspan=2, sticky="w", pady=(10, 0))

        ttk.Button(controls, text=self.tr("trainer.button.exportZip"), command=self.export_zip).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0)
        )
        ttk.Button(controls, text=self.tr("trainer.button.openUnlabeled"), command=lambda: self.open_dir(self.base_dir_var.get() + "/images/unlabeled")).grid(
            row=4, column=2, sticky="ew", pady=(10, 0)
        )
        ttk.Button(controls, text=self.tr("trainer.button.openLabeled"), command=lambda: self.open_dir(self.base_dir_var.get())).grid(
            row=4, column=3, sticky="ew", pady=(10, 0)
        )
        ttk.Button(controls, text=self.tr("trainer.button.refresh"), command=self.refresh_stats).grid(
            row=4, column=4, sticky="ew", pady=(10, 0)
        )

        tips = ttk.LabelFrame(main, text=self.tr("trainer.frame.tips"), padding=10)
        tips.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        tips.columnconfigure(0, weight=1)
        tips.rowconfigure(1, weight=1)

        ttk.Label(
            tips,
            text=self.tr("trainer.tips"),
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
        self.unlabeled_var.set(self.tr("trainer.stats.unlabeled", count=stats["unlabeled_images"]))
        self.train_var.set(
            self.tr("trainer.stats.train", images=stats["train_images"], labels=stats["train_labels"])
        )
        self.val_var.set(
            self.tr("trainer.stats.val", images=stats["val_images"], labels=stats["val_labels"])
        )
        self.pairs_var.set(self.tr("trainer.stats.pairs", count=stats["labeled_pairs"]))

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

        self.log(
            self.tr(
                "trainer.process.started",
                kind=self.tr(f"trainer.task.{process_kind}", default=process_kind),
                command=" ".join(cmd),
            )
        )
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
                self.root.after(
                    0,
                    self.log,
                    self.tr(
                        "trainer.process.line",
                        kind=self.tr(f"trainer.task.{process_kind}", default=process_kind),
                        line=clean,
                    ),
                )
        code = proc.wait()
        self.root.after(0, self._on_process_done, process_kind, code)

    def _on_process_done(self, process_kind, code):
        if process_kind == "collect":
            self.collect_proc = None
        else:
            self.label_proc = None
        self.log(
            self.tr(
                "trainer.process.done",
                kind=self.tr(f"trainer.task.{process_kind}", default=process_kind),
                code=code,
            )
        )
        self.refresh_stats()

    def start_collect(self):
        if self.collect_proc and self.collect_proc.poll() is None:
            messagebox.showinfo(self.tr("trainer.msg.info"), self.tr("trainer.msg.collectRunning"))
            return

        try:
            fps = float(self.fps_var.get().strip())
            max_count = int(self.max_var.get().strip())
        except ValueError:
            messagebox.showerror(self.tr("trainer.msg.paramError"), self.tr("trainer.msg.invalidCollectArgs"))
            return

        cmd = self.build_runner_command(
            "collect",
            [
                "--fps", str(fps),
                "--max", str(max_count),
                * (["--roi"] if self.roi_var.get() else []),
                "--base-dir", self.base_dir_var.get(),
            ],
        )
        self.start_process(cmd, "collect")

    def stop_collect(self):
        if not self.collect_proc or self.collect_proc.poll() is not None:
            messagebox.showinfo(self.tr("trainer.msg.info"), self.tr("trainer.msg.collectIdle"))
            return
        self.collect_proc.terminate()
        self.log(self.tr("trainer.msg.stopCollectRequested"))

    def start_label(self, relabel=False):
        if self.label_proc and self.label_proc.poll() is None:
            messagebox.showinfo(self.tr("trainer.msg.info"), self.tr("trainer.msg.labelRunning"))
            return

        try:
            split = float(self.split_var.get().strip())
        except ValueError:
            messagebox.showerror(self.tr("trainer.msg.paramError"), self.tr("trainer.msg.invalidSplit"))
            return

        label_args = [
            "--split", str(split),
            "--base-dir", self.base_dir_var.get(),
        ]
        if relabel:
            label_args.append("--relabel")
            
        # 添加自动打标相关参数
        model_path = self.predict_model_var.get().strip()
        if model_path:
            label_args.extend(["--predict-model", model_path])
            if self.auto_predict_var.get():
                label_args.append("--auto-predict")

        cmd = self.build_runner_command("label", label_args)
        self.start_process(cmd, "label")

    def export_zip(self):
        self.refresh_stats()
        default_name = build_export_name()
        zip_path = filedialog.asksaveasfilename(
            title=self.tr("trainer.msg.exportDialogTitle"),
            defaultextension=".zip",
            initialdir=APP_ROOT,
            initialfile=default_name,
            filetypes=[(self.tr("trainer.msg.zipFileType"), "*.zip")],
        )
        if not zip_path:
            return

        try:
            count = export_labeled_dataset(zip_path)
        except ValueError as exc:
            messagebox.showinfo(self.tr("trainer.msg.noExportData"), str(exc))
            return
        except Exception as exc:
            messagebox.showerror(self.tr("trainer.msg.exportFailed"), str(exc))
            return

        self.log(self.tr("trainer.msg.exported", count=count, path=zip_path))
        messagebox.showinfo(
            self.tr("trainer.msg.exportDone"),
            self.tr("trainer.msg.exportDoneDetail", count=count, path=zip_path),
        )

    def open_dir(self, path):
        os.makedirs(path, exist_ok=True)
        os.startfile(path)

    def on_close(self):
        running = []
        if self.collect_proc and self.collect_proc.poll() is None:
            running.append(self.tr("trainer.task.collect"))
        if self.label_proc and self.label_proc.poll() is None:
            running.append(self.tr("trainer.task.label"))

        if running:
            if not messagebox.askyesno(
                self.tr("trainer.msg.exitConfirm"),
                self.tr("trainer.msg.exitRunning", tasks=", ".join(running)),
            ):
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
    YoloTrainerGUI(root)
    root.mainloop()


def main(argv=None):
    parser = build_parser()
    args, unknown = parser.parse_known_args(argv)
    # 如果是子工具模式，将未知参数原样传递下去
    if dispatch_tool(args, unknown):
        return
    launch_gui()


if __name__ == "__main__":
    main()
