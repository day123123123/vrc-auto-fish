"""
日志工具模块
============
支持控制台输出 + GUI 队列推送 + 文件保存。
"""

import os
import time
import queue


class Logger:
    """日志器 — 控制台打印 + queue 推送 + 内存缓存(供保存)"""

    def __init__(self):
        self.log_queue: queue.Queue = queue.Queue()
        self._lines: list[str] = []

    def info(self, msg: str):
        self._emit("INFO", msg)

    def warning(self, msg: str):
        self._emit("WARN", msg)

    def error(self, msg: str):
        self._emit("ERROR", msg)

    def debug(self, msg: str):
        self._emit("DEBUG", msg)

    def _emit(self, level: str, msg: str):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}][{level:>5s}] {msg}"
        print(line)
        self._lines.append(line)
        try:
            self.log_queue.put_nowait(line)
        except queue.Full:
            pass

    def save(self, path: str):
        """将当前所有日志覆盖写入文件"""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(self._lines))
                f.write("\n")
        except Exception as e:
            try:
                from utils.i18n import t
                print(t("log.logger_save_fail", e=e))
            except Exception:
                print(f"[Logger] Failed to save log: {e}")

    def clear(self):
        """清空内存中的日志缓存"""
        self._lines.clear()


# 全局单例
log = Logger()
