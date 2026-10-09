"""
BOOTH ランチャーのツール検出まわりのテスト
=========================================
Tk を起動せずに, git 検出とインストール案内のロジックだけを検証する。
"""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import booth_launcher
from booth_launcher import (
    GIT_DOWNLOAD_URL,
    GIT_PATH_CANDIDATES,
    PYTHON_DOWNLOAD_URL,
    BoothLauncherApp,
    LauncherError,
)

def make_app(**attrs):
    """Tk を使わずに BoothLauncherApp のインスタンスを作る。"""
    app = BoothLauncherApp.__new__(BoothLauncherApp)
    app.logs = []
    app.statuses = []
    app.prompts = []
    app._append_log = app.logs.append
    app._set_status = app.statuses.append
    app.git_cmd = ["git"]
    app.python_cmd = None
    for key, value in attrs.items():
        setattr(app, key, value)
    return app


def make_tool_app(**attrs):
    """_prompt_missing_tool を記録用に差し替えたインスタンス。"""
    app = make_app(**attrs)
    app._prompt_missing_tool = (
        lambda title, detail, url: app.prompts.append((title, url))
    )
    return app


class FindGitCommandTests(unittest.TestCase):
    def test_uses_git_from_path(self):
        with patch.object(booth_launcher.shutil, "which", return_value=r"C:\Git\cmd\git.exe"):
            self.assertEqual(
                BoothLauncherApp._find_git_command(), [r"C:\Git\cmd\git.exe"]
            )

    def test_falls_back_to_known_install_locations(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_git = Path(tmp) / "git.exe"
            fake_git.write_text("", encoding="utf-8")
            with patch.object(booth_launcher.shutil, "which", return_value=None), \
                    patch.object(booth_launcher, "GIT_PATH_CANDIDATES", [str(fake_git)]):
                found = BoothLauncherApp._find_git_command()
            self.assertEqual(found, [str(fake_git)])

    def test_returns_none_when_git_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "no-such-git.exe")
            with patch.object(booth_launcher.shutil, "which", return_value=None), \
                    patch.object(booth_launcher, "GIT_PATH_CANDIDATES", [missing]):
                self.assertIsNone(BoothLauncherApp._find_git_command())

    def test_known_locations_cover_common_windows_installs(self):
        joined = " ".join(GIT_PATH_CANDIDATES).lower()
        for marker in ("programfiles", "localappdata", "scoop", "chocolatey"):
            self.assertIn(marker, joined)


class MissingToolPromptTests(unittest.TestCase):
    def test_opens_download_page_when_user_agrees(self):
        app = make_app()
        app._prompt_yes_no = lambda title, message: True
        with patch.object(booth_launcher.webbrowser, "open") as open_mock:
            app._prompt_missing_tool("Git が見つかりません", "詳細", GIT_DOWNLOAD_URL)

        open_mock.assert_called_once_with(GIT_DOWNLOAD_URL)
        self.assertTrue(any(GIT_DOWNLOAD_URL in line for line in app.logs))

    def test_keeps_url_in_log_when_user_declines(self):
        app = make_app()
        app._prompt_yes_no = lambda title, message: False
        with patch.object(booth_launcher.webbrowser, "open") as open_mock:
            app._prompt_missing_tool("Git が見つかりません", "詳細", GIT_DOWNLOAD_URL)

        open_mock.assert_not_called()
        self.assertTrue(any(GIT_DOWNLOAD_URL in line for line in app.logs))

    def test_browser_failure_is_reported_not_raised(self):
        app = make_app()
        app._prompt_yes_no = lambda title, message: True
        with patch.object(booth_launcher.webbrowser, "open", side_effect=OSError("boom")):
            app._prompt_missing_tool("Git が見つかりません", "詳細", GIT_DOWNLOAD_URL)

        self.assertTrue(any("[警告]" in line for line in app.logs))

    def test_prompt_message_contains_the_url(self):
        app = make_app()
        seen = {}

        def fake_prompt(title, message):
            seen["title"] = title
            seen["message"] = message
            return False

        app._prompt_yes_no = fake_prompt
        app._prompt_missing_tool(
            "Git が見つかりません", "Git を入れてください。", GIT_DOWNLOAD_URL
        )
        self.assertEqual(seen["title"], "Git が見つかりません")
        self.assertIn(GIT_DOWNLOAD_URL, seen["message"])
        self.assertIn("Git を入れてください。", seen["message"])


class EnsureToolsTests(unittest.TestCase):
    def test_raises_and_prompts_when_git_missing(self):
        app = make_tool_app()
        with patch.object(BoothLauncherApp, "_find_git_command", return_value=None):
            with self.assertRaises(LauncherError):
                app._ensure_tools()

        self.assertEqual(len(app.prompts), 1)
        self.assertEqual(app.prompts[0][1], GIT_DOWNLOAD_URL)
        self.assertTrue(any("[エラー]" in line for line in app.logs))

    def test_remembers_detected_git_path(self):
        app = make_tool_app()
        with patch.object(
            BoothLauncherApp, "_find_git_command",
            return_value=[r"C:\Git\cmd\git.exe"],
        ), patch.object(
            BoothLauncherApp, "_find_python_command",
            return_value=["py", "-3.12"],
        ):
            app._ensure_tools()

        self.assertEqual(app.git_cmd, [r"C:\Git\cmd\git.exe"])
        self.assertEqual(app.python_cmd, ["py", "-3.12"])
        self.assertEqual(app.prompts, [])
        self.assertTrue(any(r"C:\Git\cmd\git.exe" in line for line in app.logs))

    def test_prompts_for_missing_python(self):
        app = make_tool_app()
        with patch.object(
            BoothLauncherApp, "_find_git_command", return_value=["git"],
        ), patch.object(BoothLauncherApp, "_find_python_command", return_value=None):
            with self.assertRaises(LauncherError):
                app._ensure_tools()

        self.assertEqual(len(app.prompts), 1)
        self.assertEqual(app.prompts[0][1], PYTHON_DOWNLOAD_URL)


class GitCommandUsageTests(unittest.TestCase):
    """検出した git の絶対パスが実際のコマンドに使われること。"""

    def test_clone_uses_detected_git_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp) / "repo"
            app = make_app(
                config=SimpleNamespace(
                    repo_dir=repo_dir,
                    branch="main",
                    repo_url="https://example.invalid/repo.git",
                ),
            )
            app.git_cmd = [r"C:\Git\cmd\git.exe"]
            app._run_command = MagicMock()
            app._get_pending_updates = MagicMock(return_value=(0, []))

            app._clone_or_update_repo()

        command = app._run_command.call_args.args[0]
        self.assertEqual(command[0], r"C:\Git\cmd\git.exe")
        self.assertIn("clone", command)


if __name__ == "__main__":
    unittest.main()
