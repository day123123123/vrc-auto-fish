"""
多颜色鱼标注工具
================
支持多颜色鱼、bar、track、progress，并兼容旧 `fish` 单类标注。
"""

import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fish_trainer.classes import (
    CLASS_COLORS,
    CLASS_NAMES,
    CLASS_SHORTCUTS,
    DISPLAY_NAMES,
    KEY_TO_CLASS,
    OVERLAY_NAMES,
)
from fish_trainer.console import safe_print
from fish_trainer.paths import TRAIN_IMG, TRAIN_LBL, UNLABELED, VAL_IMG, VAL_LBL, ensure_dataset_dirs
from trainer_common.labeling import (
    build_label_parser,
    list_relabel_entries,
    list_unlabeled_entries,
    load_existing_labels,
    save_new_labeled_entry,
    write_yolo_labels,
)

drawing = False
ix = iy = 0
boxes = []
current_class = 0
img_display = None
img_orig = None


def short_help():
    return (
        "[F]=black [1-9,0]=fish colors [?]=question [B]=bar [T]=track [P]=progress "
        "[N/M]=prev/next [Z]=undo [X]=clear [H]=help [S]=save [D]=skip [Q]=quit"
    )


def print_help():
    safe_print("=" * 72)
    safe_print("  多颜色鱼标注快捷键")
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
        cv2.imshow("Fish Trainer Label Tool", tmp)
    elif event == cv2.EVENT_LBUTTONUP:
        drawing = False
        x1, y1 = min(ix, x), min(iy, y)
        x2, y2 = max(ix, x), max(iy, y)
        if x2 - x1 > 5 and y2 - y1 > 5:
            boxes.append((current_class, x1, y1, x2, y2))
            draw_overlay()
            cv2.imshow("Fish Trainer Label Tool", img_display)


def label_loop(file_pairs, save_func, mode_name):
    global current_class, boxes, img_orig

    cv2.namedWindow("Fish Trainer Label Tool", cv2.WINDOW_NORMAL)
    cv2.setMouseCallback("Fish Trainer Label Tool", mouse_cb)
    print_help()

    labeled = 0
    for idx, entry in enumerate(file_pairs):
        img_path = entry["img_path"]
        lbl_path = entry["lbl_path"]
        img_orig = cv2.imread(img_path)
        if img_orig is None:
            continue
        h, w = img_orig.shape[:2]
        boxes = load_existing_labels(lbl_path, w, h) if lbl_path else []
        current_class = 12 if boxes else 0

        cv2.resizeWindow("Fish Trainer Label Tool", min(w, 1280), int(h * min(w, 1280) / w))
        draw_overlay()
        cv2.imshow("Fish Trainer Label Tool", img_display)

        safe_print(f"[{idx + 1}/{len(file_pairs)}] {os.path.basename(img_path)} ({w}x{h})")
        while True:
            key = cv2.waitKey(0) & 0xFF
            lower_key = ord(chr(key).lower()) if key < 256 else key

            if lower_key in KEY_TO_CLASS:
                current_class = KEY_TO_CLASS[lower_key]
                safe_print(f"    类别 -> {DISPLAY_NAMES.get(current_class)} ({CLASS_NAMES.get(current_class)})")
                draw_overlay()
                cv2.imshow("Fish Trainer Label Tool", img_display)
            elif key in (ord("n"), ord("N")):
                current_class = (current_class + 1) % len(CLASS_NAMES)
                safe_print(f"    类别 -> {DISPLAY_NAMES.get(current_class)} ({CLASS_NAMES.get(current_class)})")
                draw_overlay()
                cv2.imshow("Fish Trainer Label Tool", img_display)
            elif key in (ord("m"), ord("M")):
                current_class = (current_class - 1) % len(CLASS_NAMES)
                safe_print(f"    类别 -> {DISPLAY_NAMES.get(current_class)} ({CLASS_NAMES.get(current_class)})")
                draw_overlay()
                cv2.imshow("Fish Trainer Label Tool", img_display)
            elif key in (ord("z"), ord("Z")):
                if boxes:
                    removed = boxes.pop()
                    safe_print(f"    撤销: {DISPLAY_NAMES.get(removed[0], '?')}")
                    draw_overlay()
                    cv2.imshow("Fish Trainer Label Tool", img_display)
            elif key in (ord("x"), ord("X")):
                boxes.clear()
                safe_print("    已清空当前图片全部标注")
                draw_overlay()
                cv2.imshow("Fish Trainer Label Tool", img_display)
            elif key in (ord("h"), ord("H")):
                print_help()
            elif key in (ord("s"), ord("S"), 13):
                if not boxes:
                    safe_print("    [跳过] 没有标注框")
                    break
                save_func(entry)
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
    parser = build_label_parser("多颜色鱼标注工具")
    parser.add_argument("--base-dir", type=str, default="", help="数据目录（可选，默认使用配置路径）")
    return parser


def main(argv=None):
    global TRAIN_IMG, TRAIN_LBL, UNLABELED, VAL_IMG, VAL_LBL
    parser = build_parser()
    args = parser.parse_args(argv)

    # 如果提供了自定义数据目录，更新全局路径
    if args.base_dir:
        import fish_trainer.paths
        base_dir = os.path.abspath(args.base_dir)
        fish_trainer.paths.BASE = base_dir
        fish_trainer.paths.UNLABELED = os.path.join(base_dir, "images", "unlabeled")
        fish_trainer.paths.TRAIN_IMG = os.path.join(base_dir, "images", "train")
        fish_trainer.paths.TRAIN_LBL = os.path.join(base_dir, "labels", "train")
        fish_trainer.paths.VAL_IMG = os.path.join(base_dir, "images", "val")
        fish_trainer.paths.VAL_LBL = os.path.join(base_dir, "labels", "val")
        # 更新本模块中的全局变量
        TRAIN_IMG = fish_trainer.paths.TRAIN_IMG
        TRAIN_LBL = fish_trainer.paths.TRAIN_LBL
        UNLABELED = fish_trainer.paths.UNLABELED
        VAL_IMG = fish_trainer.paths.VAL_IMG
        VAL_LBL = fish_trainer.paths.VAL_LBL

    ensure_dataset_dirs()
    if args.relabel:
        pairs = list_relabel_entries(TRAIN_IMG, TRAIN_LBL, VAL_IMG, VAL_LBL)
        if not pairs:
            safe_print("[提示] train/ val/ 中没有可补标的图片")
            return

        def save_inplace(entry):
            write_yolo_labels(entry["lbl_path"], img_orig.shape, boxes)

        label_loop(pairs, save_inplace, mode_name="补标")
        return

    file_pairs = list_unlabeled_entries(UNLABELED)
    if not file_pairs:
        safe_print(f"[提示] {UNLABELED} 中没有未标注图片")
        return

    def save_new(entry):
        save_new_labeled_entry(
            entry,
            img_orig.shape,
            boxes,
            args.split,
            TRAIN_IMG,
            TRAIN_LBL,
            VAL_IMG,
            VAL_LBL,
            safe_print,
        )

    label_loop(file_pairs, save_new, mode_name="标注")


if __name__ == "__main__":
    main()
