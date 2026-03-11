"""
YOLO 多颜色鱼标注工具
======================
保留 yolo 命令入口，但统一使用多颜色鱼类别体系。
"""

import argparse
import os
import random
import shutil
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yolo.classes import (
    CLASS_COLORS,
    CLASS_NAMES,
    CLASS_SHORTCUTS,
    DISPLAY_NAMES,
    KEY_TO_CLASS,
    OVERLAY_NAMES,
)
from yolo.console import safe_print
from yolo.paths import TRAIN_IMG, TRAIN_LBL, UNLABELED, VAL_IMG, VAL_LBL, ensure_dataset_dirs

drawing = False
ix = iy = 0
boxes = []
current_class = 0
img_display = None
img_orig = None


def short_help():
    return (
        "[F]=generic [1-9]=fish colors [B]=bar [T]=track [P]=progress [K]=hook "
        "[N/M]=prev/next [Z]=undo [X]=clear [H]=help [S]=save [D]=skip [Q]=quit"
    )


def print_help():
    safe_print("=" * 72)
    safe_print("  YOLO 多颜色鱼标注快捷键")
    safe_print("=" * 72)
    for cls_id in sorted(CLASS_NAMES):
        safe_print(
            f"  [{CLASS_SHORTCUTS.get(cls_id, '?')}] "
            f"{DISPLAY_NAMES.get(cls_id, CLASS_NAMES[cls_id])} ({CLASS_NAMES[cls_id]})"
        )
    safe_print("  [N]/[M] 下一个/上一个类别")
    safe_print("  [Z] 撤销  [X] 清空当前图片标注")
    safe_print("  [S]/[Enter] 保存  [D] 跳过  [Q]/[Esc] 退出")
    safe_print("=" * 72)


def draw_overlay():
    global img_display
    img_display = img_orig.copy()
    h, _w = img_display.shape[:2]
    legend_y = 20

    for cls, x1, y1, x2, y2 in boxes:
        color = CLASS_COLORS.get(cls, (128, 128, 128))
        label = OVERLAY_NAMES.get(cls, CLASS_NAMES.get(cls, "?"))
        cv2.rectangle(img_display, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            img_display,
            f"{label} ({cls})",
            (x1, max(16, y1 - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
        )

    for cls_id in sorted(CLASS_NAMES):
        color = CLASS_COLORS.get(cls_id, (128, 128, 128))
        marker = ">" if cls_id == current_class else " "
        legend = f"{marker}[{CLASS_SHORTCUTS.get(cls_id, '?')}] {OVERLAY_NAMES.get(cls_id)}"
        cv2.putText(
            img_display,
            legend,
            (8, legend_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            color,
            1,
        )
        legend_y += 18

    cv2.putText(
        img_display,
        f"class: {OVERLAY_NAMES.get(current_class, '?')} | boxes: {len(boxes)} | {short_help()}",
        (5, h - 10),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (0, 255, 255),
        1,
    )


def mouse_cb(event, x, y, flags, param):
    global drawing, ix, iy
    del flags, param

    if event == cv2.EVENT_LBUTTONDOWN:
        drawing = True
        ix, iy = x, y
    elif event == cv2.EVENT_MOUSEMOVE and drawing:
        tmp = img_display.copy()
        color = CLASS_COLORS.get(current_class, (128, 128, 128))
        cv2.rectangle(tmp, (ix, iy), (x, y), color, 2)
        cv2.imshow("YOLO Label Tool", tmp)
    elif event == cv2.EVENT_LBUTTONUP:
        drawing = False
        x1, y1 = min(ix, x), min(iy, y)
        x2, y2 = max(ix, x), max(iy, y)
        if x2 - x1 > 5 and y2 - y1 > 5:
            boxes.append((current_class, x1, y1, x2, y2))
            draw_overlay()
            cv2.imshow("YOLO Label Tool", img_display)


def load_existing_labels(lbl_path, img_w, img_h):
    loaded = []
    if not os.path.exists(lbl_path):
        return loaded
    with open(lbl_path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) < 5:
                continue
            cls = int(parts[0])
            cx, cy, bw, bh = map(float, parts[1:5])
            x1 = int((cx - bw / 2) * img_w)
            y1 = int((cy - bh / 2) * img_h)
            x2 = int((cx + bw / 2) * img_w)
            y2 = int((cy + bh / 2) * img_h)
            loaded.append((cls, x1, y1, x2, y2))
    return loaded


def write_yolo_labels(lbl_path):
    h, w = img_orig.shape[:2]
    with open(lbl_path, "w", encoding="utf-8") as f:
        for cls, x1, y1, x2, y2 in boxes:
            cx = ((x1 + x2) / 2) / w
            cy = ((y1 + y2) / 2) / h
            bw = (x2 - x1) / w
            bh = (y2 - y1) / h
            f.write(f"{cls} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n")


def label_loop(file_pairs, save_func, mode_name):
    global current_class, boxes, img_orig

    cv2.namedWindow("YOLO Label Tool", cv2.WINDOW_NORMAL)
    cv2.setMouseCallback("YOLO Label Tool", mouse_cb)
    print_help()

    labeled = 0
    for idx, (img_path, lbl_path) in enumerate(file_pairs):
        img_orig = cv2.imread(img_path)
        if img_orig is None:
            continue
        h, w = img_orig.shape[:2]
        boxes = load_existing_labels(lbl_path, w, h) if lbl_path else []
        current_class = 12 if boxes else 0

        cv2.resizeWindow("YOLO Label Tool", min(w, 1280), int(h * min(w, 1280) / w))
        draw_overlay()
        cv2.imshow("YOLO Label Tool", img_display)

        safe_print(f"[{idx + 1}/{len(file_pairs)}] {os.path.basename(img_path)} ({w}x{h})")
        while True:
            key = cv2.waitKey(0) & 0xFF
            lower_key = ord(chr(key).lower()) if key < 256 else key

            if lower_key in KEY_TO_CLASS:
                current_class = KEY_TO_CLASS[lower_key]
                safe_print(f"    类别 -> {DISPLAY_NAMES.get(current_class)} ({CLASS_NAMES.get(current_class)})")
                draw_overlay()
                cv2.imshow("YOLO Label Tool", img_display)
            elif key in (ord("n"), ord("N")):
                current_class = (current_class + 1) % len(CLASS_NAMES)
                safe_print(f"    类别 -> {DISPLAY_NAMES.get(current_class)} ({CLASS_NAMES.get(current_class)})")
                draw_overlay()
                cv2.imshow("YOLO Label Tool", img_display)
            elif key in (ord("m"), ord("M")):
                current_class = (current_class - 1) % len(CLASS_NAMES)
                safe_print(f"    类别 -> {DISPLAY_NAMES.get(current_class)} ({CLASS_NAMES.get(current_class)})")
                draw_overlay()
                cv2.imshow("YOLO Label Tool", img_display)
            elif key in (ord("z"), ord("Z")):
                if boxes:
                    removed = boxes.pop()
                    safe_print(f"    撤销: {DISPLAY_NAMES.get(removed[0], '?')}")
                    draw_overlay()
                    cv2.imshow("YOLO Label Tool", img_display)
            elif key in (ord("x"), ord("X")):
                boxes.clear()
                safe_print("    已清空当前图片全部标注")
                draw_overlay()
                cv2.imshow("YOLO Label Tool", img_display)
            elif key in (ord("h"), ord("H")):
                print_help()
            elif key in (ord("s"), ord("S"), 13):
                if not boxes:
                    safe_print("    [跳过] 没有标注框")
                    break
                save_func(img_path, lbl_path)
                labeled += 1
                safe_print(f"    [保存] {len(boxes)} 个框")
                break
            elif key in (ord("d"), ord("D")):
                safe_print("    [跳过] 此图不修改")
                break
            elif key in (ord("q"), ord("Q"), 27):
                cv2.destroyAllWindows()
                safe_print(f"\n[退出] 共{mode_name} {labeled} 张")
                return

    cv2.destroyAllWindows()
    safe_print(f"\n[完成] 共{mode_name} {labeled} 张")


def build_parser():
    parser = argparse.ArgumentParser(description="YOLO 多颜色鱼标注工具")
    parser.add_argument("--split", type=float, default=0.2, help="验证集比例")
    parser.add_argument("--relabel", action="store_true", help="重新标注已有 train/val 数据")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    ensure_dataset_dirs()
    if args.relabel:
        pairs = []
        for img_dir, lbl_dir in ((TRAIN_IMG, TRAIN_LBL), (VAL_IMG, VAL_LBL)):
            if not os.path.isdir(img_dir):
                continue
            for name in sorted(os.listdir(img_dir)):
                if name.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
                    pairs.append((
                        os.path.join(img_dir, name),
                        os.path.join(lbl_dir, os.path.splitext(name)[0] + ".txt"),
                    ))
        if not pairs:
            safe_print("[提示] train/ val/ 中没有可补标的图片")
            return

        def save_inplace(_img_path, lbl_path):
            write_yolo_labels(lbl_path)

        label_loop(pairs, save_inplace, mode_name="补标")
        return

    files = sorted(
        f for f in os.listdir(UNLABELED)
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp"))
    )
    if not files:
        safe_print(f"[提示] {UNLABELED} 中没有未标注图片")
        return

    def save_new(img_path, _unused_lbl):
        is_val = random.random() < args.split
        dst_img_dir = VAL_IMG if is_val else TRAIN_IMG
        dst_lbl_dir = VAL_LBL if is_val else TRAIN_LBL
        name = os.path.splitext(os.path.basename(img_path))[0]
        lbl_path = os.path.join(dst_lbl_dir, name + ".txt")
        write_yolo_labels(lbl_path)
        shutil.move(img_path, os.path.join(dst_img_dir, os.path.basename(img_path)))
        safe_print(f"      -> {'val' if is_val else 'train'}/")

    file_pairs = [(os.path.join(UNLABELED, name), None) for name in files]
    label_loop(file_pairs, save_new, mode_name="标注")


if __name__ == "__main__":
    main()
