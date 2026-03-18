"""
BOOTH launcher for Japanese users.

This launcher:
1. clones or updates the source repository,
2. creates a local virtual environment,
3. installs dependencies on every launch,
4. starts the main application.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk


DEFAULT_CONFIG = {
    "window_title": "VRC Auto Fish Launcher",
    "repo_url": "https://github.com/day123123123/vrc-auto-fish.git",
    "branch": "main",
    "app_dir_name": "VRC Auto Fish Booth",
    "repo_dir_name": "repo",
    "venv_dir_name": ".venv",
    "entrypoint": "main.py",
    "show_update_prompt": True,
    "update_log_count": 20,
    "auto_close_after_launch_ms": 1200,
}

PYTHON_CANDIDATES = [
    ["py", "-3.12"],
    ["py", "-3.11"],
    ["py", "-3.10"],
    ["py", "-3"],
    ["python"],
]

LEGACY_GPU_MARKERS = [
    "GTX 6",
    "GTX 7",
    "GTX 8",
    "GTX 9",
    "GTX 10",
    "GT 7",
    "GT 8",
    "GT 9",
    "GT 10",
    "Quadro K",
    "Quadro M",
    "Quadro P",
    "Tesla K",
    "Tesla M",
    "Tesla P",
    "Tesla V",
    "TITAN X",
    "TITAN XP",
    "TITAN V",
]


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def launcher_config_path() -> Path:
    return app_base_dir() / "booth_launcher_config.json"


@dataclass
class LauncherConfig:
    window_title: str
    repo_url: str
    branch: str
    app_dir_name: str
    repo_dir_name: str
    venv_dir_name: str
    entrypoint: str
    show_update_prompt: bool
    update_log_count: int
    auto_close_after_launch_ms: int

    @classmethod
    def load(cls) -> "LauncherConfig":
        data = dict(DEFAULT_CONFIG)
        path = launcher_config_path()
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                data.update(loaded)
        return cls(**data)

    @property
    def app_root(self) -> Path:
        local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(local_app_data) / self.app_dir_name

    @property
    def repo_dir(self) -> Path:
        return self.app_root / self.repo_dir_name

    @property
    def venv_dir(self) -> Path:
        return self.repo_dir / self.venv_dir_name

    @property
    def venv_python(self) -> Path:
        return self.venv_dir / "Scripts" / "python.exe"


class LauncherError(RuntimeError):
    pass


class BoothLauncherApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.config = LauncherConfig.load()
        self.root.title(self.config.window_title)
        self.root.geometry("820x560")
        self.root.minsize(720, 460)

        self.status_var = tk.StringVar(value="初期化中...")
        self.retry_btn = None
        self.close_btn = None
        self.log_text = None
        self._build_ui()

        self.python_cmd: list[str] | None = None
        self.worker = threading.Thread(target=self._worker_main, daemon=True)
        self.worker.start()

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        ttk.Label(
            main,
            text="VRC Auto Fish 自動アップデーター",
            font=("Yu Gothic UI", 14, "bold"),
        ).pack(anchor="w")

        ttk.Label(
            main,
            text="起動時にソース更新・依存関係インストール・本体起動を自動で行います。",
        ).pack(anchor="w", pady=(4, 10))

        status_frame = ttk.Frame(main)
        status_frame.pack(fill="x")

        ttk.Label(status_frame, text="状態:").pack(side="left")
        ttk.Label(status_frame, textvariable=self.status_var).pack(side="left", padx=(6, 0))

        self.log_text = tk.Text(main, wrap="word", height=24, state="disabled")
        self.log_text.pack(fill="both", expand=True, pady=(10, 10))

        button_row = ttk.Frame(main)
        button_row.pack(fill="x")

        self.retry_btn = ttk.Button(button_row, text="再試行", command=self.retry, state="disabled")
        self.retry_btn.pack(side="left")

        self.close_btn = ttk.Button(button_row, text="閉じる", command=self.root.destroy)
        self.close_btn.pack(side="right")

    def retry(self):
        if self.worker.is_alive():
            return
        self.retry_btn.config(state="disabled")
        self._set_status("再試行中...")
        self.worker = threading.Thread(target=self._worker_main, daemon=True)
        self.worker.start()

    def _append_log(self, message: str):
        def update():
            self.log_text.config(state="normal")
            self.log_text.insert("end", message.rstrip() + "\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")

        self.root.after(0, update)

    def _set_status(self, message: str):
        self.root.after(0, lambda: self.status_var.set(message))

    def _set_retry_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.root.after(0, lambda: self.retry_btn.config(state=state))

    def _worker_main(self):
        try:
            self._set_retry_enabled(False)
            self._run_flow()
        except Exception as exc:
            self._append_log("")
            self._append_log(f"[エラー] {exc}")
            self._set_status("失敗しました")
            self._set_retry_enabled(True)
            self.root.after(
                0,
                lambda: messagebox.showerror(
                    "VRC Auto Fish Launcher",
                    f"起動に失敗しました。\n\n{exc}",
                ),
            )

    def _run_flow(self):
        self._append_log("[開始] 起動処理を開始します。")
        self._ensure_tools()
        self._prepare_directories()
        self._clone_or_update_repo()
        self._ensure_venv()
        self._upgrade_pip_tooling()
        self._install_torch()
        self._install_requirements()
        self._launch_app()
        self._set_status("本体を起動しました")
        self._append_log("[完了] 本体を起動しました。ランチャーを閉じます。")
        self.root.after(self.config.auto_close_after_launch_ms, self.root.destroy)

    def _ensure_tools(self):
        self._set_status("必要ツールを確認中...")
        self._append_log("[確認] Git を確認しています...")
        if not self._command_available(["git", "--version"]):
            raise LauncherError(
                "Git が見つかりません。Git for Windows をインストールしてください:\n"
                "https://git-scm.com/download/win"
            )

        self._append_log("[確認] Python を確認しています...")
        self.python_cmd = self._find_python_command()
        if self.python_cmd is None:
            raise LauncherError(
                "Python 3.10 以上が見つかりません。Python をインストールしてください:\n"
                "https://www.python.org/downloads/"
            )
        self._append_log(f"[OK] 使用する Python: {' '.join(self.python_cmd)}")

    def _prepare_directories(self):
        self._set_status("作業フォルダを準備中...")
        self.config.app_root.mkdir(parents=True, exist_ok=True)
        self._append_log(f"[情報] 作業フォルダ: {self.config.app_root}")

    def _clone_or_update_repo(self):
        self._set_status("ソースコードを同期中...")
        repo_dir = self.config.repo_dir
        if not (repo_dir / ".git").exists():
            if repo_dir.exists():
                backup_dir = repo_dir.with_name(repo_dir.name + ".old")
                if backup_dir.exists():
                    self._remove_tree(backup_dir)
                repo_dir.rename(backup_dir)
                self._append_log(f"[情報] 既存フォルダを退避しました: {backup_dir}")
            self._append_log("[取得] リポジトリを初回クローンします...")
            self._run_command(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    self.config.branch,
                    self.config.repo_url,
                    str(repo_dir),
                ]
            )
            return

        self._append_log("[更新] 既存リポジトリを更新します...")
        self._run_command(["git", "-C", str(repo_dir), "fetch", "origin", self.config.branch, "--depth", "1"])
        self._run_command(["git", "-C", str(repo_dir), "checkout", self.config.branch])
        update_count, updates = self._get_pending_updates(repo_dir)
        if update_count == 0:
            self._append_log("[更新] 新しい更新はありません。")
            return

        self._append_log(f"[更新] {update_count} 件の更新を検出しました。")
        should_update = True
        if self.config.show_update_prompt:
            summary = self._build_update_summary(update_count, updates)
            should_update = self._prompt_yes_no(
                "アップデートがあります",
                "新しい更新が見つかりました。\n\n"
                "更新内容:\n"
                f"{summary}\n\n"
                "今すぐ更新しますか？",
            )
        if not should_update:
            self._append_log("[更新] ユーザーが更新をスキップしました。")
            return

        stashed = self._stash_if_needed(repo_dir)
        self._run_command(["git", "-C", str(repo_dir), "pull", "--ff-only", "origin", self.config.branch])
        if stashed:
            self._append_log("[更新] ローカル設定を戻しています...")
            self._run_command(["git", "-C", str(repo_dir), "stash", "pop"], check=False)

    def _stash_if_needed(self, repo_dir: Path) -> bool:
        status = subprocess.run(
            ["git", "-C", str(repo_dir), "status", "--porcelain"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if not status.stdout.strip():
            return False

        self._append_log("[更新] ローカル変更を一時退避します...")
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "stash",
                "push",
                "--include-untracked",
                "--message",
                "booth-launcher-autostash",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            raise LauncherError(result.stdout.strip() or result.stderr.strip() or "git stash に失敗しました。")
        for line in (result.stdout or "").splitlines():
            self._append_log(line)
        return "No local changes to save" not in result.stdout

    def _ensure_venv(self):
        self._set_status("Python 仮想環境を準備中...")
        if self.config.venv_python.exists():
            self._append_log("[OK] 仮想環境は既に存在します。")
            return
        self._append_log("[作成] 仮想環境を作成します...")
        self._run_command([*self.python_cmd, "-m", "venv", str(self.config.venv_dir)])

    def _get_pending_updates(self, repo_dir: Path) -> tuple[int, list[str]]:
        count_result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "rev-list",
                "--count",
                f"HEAD..origin/{self.config.branch}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if count_result.returncode != 0:
            raise LauncherError(
                count_result.stdout.strip()
                or count_result.stderr.strip()
                or "更新件数の取得に失敗しました。"
            )
        total_count = int((count_result.stdout or "0").strip() or "0")
        if total_count == 0:
            return 0, []

        log_result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "log",
                f"HEAD..origin/{self.config.branch}",
                f"--pretty=format:%h %s",
                f"-n{self.config.update_log_count + 1}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if log_result.returncode != 0:
            raise LauncherError(
                log_result.stdout.strip()
                or log_result.stderr.strip()
                or "更新履歴の取得に失敗しました。"
            )
        updates = [line.strip() for line in log_result.stdout.splitlines() if line.strip()]
        return total_count, updates

    def _build_update_summary(self, total_count: int, updates: list[str]) -> str:
        visible = updates[: self.config.update_log_count]
        lines = [f"- {line}" for line in visible]
        remaining = total_count - len(visible)
        if remaining > 0:
            lines.append(f"... ほか {remaining} 件")
        return "\n".join(lines)

    def _prompt_yes_no(self, title: str, message: str) -> bool:
        result: dict[str, bool] = {"value": False}
        event = threading.Event()

        def ask():
            result["value"] = bool(messagebox.askyesno(title, message))
            event.set()

        self.root.after(0, ask)
        event.wait()
        return result["value"]

    def _upgrade_pip_tooling(self):
        self._set_status("pip を更新中...")
        self._append_log("[更新] pip / setuptools / wheel を更新します...")
        self._run_command(
            [
                str(self.config.venv_python),
                "-m",
                "pip",
                "install",
                "--upgrade",
                "pip",
                "setuptools",
                "wheel",
            ],
            cwd=self.config.repo_dir,
        )

    def _install_torch(self):
        self._set_status("PyTorch をインストール中...")
        gpu_name = self._detect_nvidia_gpu()
        index_url = "https://download.pytorch.org/whl/cpu"
        label = "CPU"
        if gpu_name:
            if self._is_legacy_gpu(gpu_name):
                index_url = "https://download.pytorch.org/whl/cu118"
                label = "CUDA 11.8"
            else:
                index_url = "https://download.pytorch.org/whl/cu128"
                label = "CUDA 12.8"
            self._append_log(f"[GPU] NVIDIA GPU を検出: {gpu_name}")
        else:
            self._append_log("[GPU] NVIDIA GPU は検出されませんでした。CPU 版を使用します。")

        self._append_log(f"[更新] PyTorch ({label}) をインストールします...")
        self._run_command(
            [
                str(self.config.venv_python),
                "-m",
                "pip",
                "install",
                "--upgrade",
                "torch",
                "torchvision",
                "--index-url",
                index_url,
            ],
            cwd=self.config.repo_dir,
        )

    def _install_requirements(self):
        self._set_status("依存関係をインストール中...")
        requirements = self.config.repo_dir / "requirements.txt"
        if not requirements.exists():
            raise LauncherError(f"requirements.txt が見つかりません: {requirements}")
        self._append_log("[更新] requirements.txt の依存関係をインストールします...")
        self._run_command(
            [
                str(self.config.venv_python),
                "-m",
                "pip",
                "install",
                "--upgrade",
                "-r",
                str(requirements),
            ],
            cwd=self.config.repo_dir,
        )

    def _launch_app(self):
        self._set_status("本体を起動中...")
        entrypoint = self.config.repo_dir / self.config.entrypoint
        if not entrypoint.exists():
            raise LauncherError(f"起動ファイルが見つかりません: {entrypoint}")
        self._append_log("[起動] 本体を起動します...")
        subprocess.Popen(
            [str(self.config.venv_python), str(entrypoint)],
            cwd=str(self.config.repo_dir),
        )

    def _run_command(self, command: list[str], cwd: Path | None = None, check: bool = True):
        self._append_log(f"$ {' '.join(command)}")
        process = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            self._append_log(line.rstrip())
        process.wait()
        if check and process.returncode != 0:
            raise LauncherError(f"コマンド失敗: {' '.join(command)}")
        return process.returncode

    @staticmethod
    def _command_available(command: list[str]) -> bool:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return result.returncode == 0

    @staticmethod
    def _find_python_command() -> list[str] | None:
        for candidate in PYTHON_CANDIDATES:
            if BoothLauncherApp._command_available(candidate + ["--version"]):
                return candidate
        return None

    @staticmethod
    def _detect_nvidia_gpu() -> str | None:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            name = line.strip()
            if name:
                return name
        return None

    @staticmethod
    def _is_legacy_gpu(gpu_name: str) -> bool:
        upper = gpu_name.upper()
        return any(marker.upper() in upper for marker in LEGACY_GPU_MARKERS)

    @staticmethod
    def _remove_tree(path: Path):
        if not path.exists():
            return
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink()
            else:
                child.rmdir()
        path.rmdir()


def main():
    root = tk.Tk()
    BoothLauncherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
