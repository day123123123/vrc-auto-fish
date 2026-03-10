"""
多颜色鱼训练数据采集
====================
独立于主程序 YOLO 目录，截图保存到 `fish_trainer/dataset/images/unlabeled/`。
"""

import argparse
import json
import os
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core.screen import ScreenCapture
from core.window import WindowManager
from fish_trainer.paths import UNLABELED, ensure_dataset_dirs


def load_saved_roi():
    try:
        with open(config.SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    if isinstance(data, dict):
        if isinstance(data.get("current"), dict):
            return data["current"].get("DETECT_ROI")
        return data.get("DETECT_ROI")
    return None


def main():
    parser = argparse.ArgumentParser(description="多颜色鱼训练数据采集")
    parser.add_argument("--fps", type=float, default=2.0, help="每秒截图数")
    parser.add_argument("--roi", action="store_true", help="只截取已保存的 ROI")
    parser.add_argument("--max", type=int, default=0, help="最大截图数量，0 表示无限")
    args = parser.parse_args()

    ensure_dataset_dirs()

    window = WindowManager(config.WINDOW_TITLE)
    screen = ScreenCapture()
    if not window.find():
        print("[错误] 未找到 VRChat 窗口，请确保游戏正在运行")
        return

    roi = load_saved_roi() if args.roi else None
    interval = 1.0 / max(args.fps, 0.1)
    count = 0

    print(f"[✓] 已连接: {window.title} (HWND={window.hwnd})")
    print(f"[保存] {UNLABELED}")
    print(f"[设置] 截图间隔: {interval:.2f}s | ROI: {'是' if roi else '否'}")
    if roi:
        print(f"[ROI] X={roi[0]} Y={roi[1]} {roi[2]}x{roi[3]}")

    try:
        while True:
            if not window.is_valid() and not window.find():
                print("[等待] VRChat 窗口未找到，5 秒后重试...")
                time.sleep(5)
                continue

            img, _ = screen.grab_window(window)
            if img is None:
                time.sleep(0.5)
                continue

            if roi:
                rx, ry, rw, rh = roi
                img = img[ry:ry + rh, rx:rx + rw]

            ts = time.strftime("%Y%m%d_%H%M%S")
            ms = int((time.time() % 1) * 1000)
            name = f"{ts}_{ms:03d}.png"
            cv2.imwrite(os.path.join(UNLABELED, name), img)
            count += 1
            h, w = img.shape[:2]
            print(f"  [{count}] {name} ({w}x{h})", end="\r")

            if args.max > 0 and count >= args.max:
                print(f"\n[完成] 已采集 {count} 张截图")
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\n[停止] 共采集 {count} 张截图 → {UNLABELED}")


if __name__ == "__main__":
    main()
