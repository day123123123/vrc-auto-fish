"""
YOLO 多颜色鱼标注工具
======================
保留 yolo 命令入口，但统一使用多颜色鱼类别体系。
支持：
1. 手动画框
2. 使用模型自动预标
3. 对选中框进行快速微调
"""

import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from yolo.classes import (
    CLASS_COLORS,
    CLASS_NAMES,
    CLASS_SHORTCUTS,
    DISPLAY_NAMES,
    KEY_TO_CLASS,
    OVERLAY_NAMES,
)
from yolo.console import safe_print
import yolo.paths
from yolo.paths import ensure_dataset_dirs
from trainer_common.labeling import (
    build_label_parser,
    list_relabel_entries,
    list_unlabeled_entries,
    load_existing_labels,
    save_new_labeled_entry,
    write_yolo_labels,
)

# 使用模块引用以支持动态路径更新
TRAIN_IMG = yolo.paths.TRAIN_IMG
TRAIN_LBL = yolo.paths.TRAIN_LBL
UNLABELED = yolo.paths.UNLABELED
VAL_IMG = yolo.paths.VAL_IMG
VAL_LBL = yolo.paths.VAL_LBL

drawing = False
ix = iy = 0
boxes = []
current_class = 0
img_display = None
img_orig = None
selected_box_idx = -1
auto_labeler = None
auto_predict_enabled = False
selection_hit_indices = ()

CLASS_NAME_TO_ID = {name: cls_id for cls_id, name in CLASS_NAMES.items()}
BOX_MOVE_STEP = 2
BOX_RESIZE_STEP = 0.05
KEY_CTRL_D = 4
KEY_BACKSPACE = 8


def short_help():
    return (
        "[F]=black [1-9,0]=fish colors [?]=question [B]=bar [T]=track [P]=progress [K]=hook "
        "[A]=auto [RClick]=cycle-hit [[/]]=box-/+ [,/.;']=move [-/J]=prev [=/D]=skip/next "
        "[Z]=undo [X]=clear [BS]=del-box [Ctrl+D]=delete [H]=help [S]=save [Q]=quit"
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
    safe_print("  [A] 使用模型自动打标当前图片（覆盖当前框）")
    safe_print("  右键点重叠区域: 在命中的框之间循环切换选中")
    safe_print("  鼠标右键点框: 选中框；右键点空白: 取消选中")
    safe_print("  选中框后左键重新拉框: 直接覆盖这个框")
    safe_print("  [[ / ]] 缩小/放大选中框")
    safe_print("  [,/.;'] 上下左右微调选中框")
    safe_print("  [- / J] 回到上一张图片")
    safe_print("  [= / D] 跳过当前图片到下一张")
    safe_print("  [N]/[M] 下一个/上一个类别")
    safe_print("  [Z] 撤销  [X] 清空当前图片标注")
    safe_print("  [Backspace] 删除当前选中的标注框")
    safe_print("  [Ctrl+D] 删除当前图片文件并跳到下一张")
    safe_print("  [S]/[Enter] 保存  [Q]/[Esc] 退出")
    safe_print("=" * 72)


def normalize_class_name(class_name):
    if class_name == "fish":
        return "fish_black"
    class_name = config.LEGACY_FISH_KEY_ALIASES.get(class_name, class_name)
    if class_name in CLASS_NAME_TO_ID:
        return class_name
    return None


def resolve_predict_device(device_name):
    normalized = config.normalize_yolo_device(device_name)
    if normalized == "cuda":
        return 0
    if normalized != "auto":
        return normalized
    try:
        import torch
        return 0 if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


class AutoLabeler:
    def __init__(self, model_path, conf=0.25, device="auto", one_per_class=True):
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError("缺少 ultralytics，无法启用自动打标") from exc

        if not os.path.exists(model_path):
            raise FileNotFoundError(f"自动打标模型不存在: {model_path}")

        self.model = YOLO(model_path)
        self.conf = conf
        self.device = resolve_predict_device(device)
        self.one_per_class = one_per_class

    def predict_boxes(self, img):
        results = self.model.predict(
            img,
            conf=self.conf,
            device=self.device,
            verbose=False,
        )
        if not results:
            return []

        result_boxes = results[0].boxes
        if result_boxes is None or len(result_boxes) == 0:
            return []

        best_by_class = {}
        predicted = []
        for box in result_boxes:
            class_idx = int(box.cls[0])
            conf = float(box.conf[0]) if box.conf is not None else 0.0
            raw_name = self.model.names.get(class_idx, f"cls{class_idx}")
            norm_name = normalize_class_name(raw_name)
            if norm_name is None:
                continue

            target_cls = CLASS_NAME_TO_ID[norm_name]
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            candidate = (
                target_cls,
                int(round(x1)),
                int(round(y1)),
                int(round(x2)),
                int(round(y2)),
                conf,
            )

            if self.one_per_class:
                prev = best_by_class.get(target_cls)
                if prev is None or conf > prev[5]:
                    best_by_class[target_cls] = candidate
            else:
                predicted.append(candidate)

        if self.one_per_class:
            predicted = list(best_by_class.values())

        predicted.sort(key=lambda item: (item[0], -item[5]))
        return [clamp_box((cls, x1, y1, x2, y2)) for cls, x1, y1, x2, y2, _conf in predicted]


def clamp_box(box):
    cls, x1, y1, x2, y2 = box
    h, w = img_orig.shape[:2]
    x1 = max(0, min(int(x1), w - 1))
    y1 = max(0, min(int(y1), h - 1))
    x2 = max(0, min(int(x2), w - 1))
    y2 = max(0, min(int(y2), h - 1))
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return (cls, x1, y1, x2, y2)


def set_selected_box(idx):
    global selected_box_idx
    if not boxes:
        selected_box_idx = -1
        return
    if idx is None or idx < 0:
        selected_box_idx = -1
        return
    selected_box_idx = max(0, min(idx, len(boxes) - 1))


def reset_hit_selection_cycle():
    global selection_hit_indices
    selection_hit_indices = ()


def clear_selected_box():
    reset_hit_selection_cycle()
    set_selected_box(-1)


def sync_selection_for_target_class(target_cls):
    if selected_box_idx < 0 or selected_box_idx >= len(boxes):
        return
    if boxes[selected_box_idx][0] != target_cls:
        clear_selected_box()


def hit_box_indices_at(x, y):
    hits = []
    for idx in range(len(boxes) - 1, -1, -1):
        _cls, x1, y1, x2, y2 = boxes[idx]
        if x1 <= x <= x2 and y1 <= y <= y2:
            hits.append(idx)
    return tuple(hits)


def select_box_at(x, y):
    global selection_hit_indices
    hit_indices = hit_box_indices_at(x, y)
    if not hit_indices:
        clear_selected_box()
        return None, 0, 0

    if hit_indices == selection_hit_indices and selected_box_idx in hit_indices:
        next_pos = (hit_indices.index(selected_box_idx) + 1) % len(hit_indices)
    else:
        selection_hit_indices = hit_indices
        next_pos = 0

    chosen_idx = hit_indices[next_pos]
    set_selected_box(chosen_idx)
    return boxes[chosen_idx][0], next_pos + 1, len(hit_indices)


def clone_boxes(src_boxes):
    return [tuple(box) for box in src_boxes]


def replace_selected_box(new_box):
    if selected_box_idx < 0 or selected_box_idx >= len(boxes):
        return False
    boxes[selected_box_idx] = clamp_box(new_box)
    return True


def get_initial_boxes(img_path, lbl_path, img_w, img_h, draft_boxes):
    if img_path in draft_boxes:
        return clone_boxes(draft_boxes[img_path])
    if lbl_path and os.path.exists(lbl_path):
        return load_existing_labels(lbl_path, img_w, img_h)
    return []


def stash_current_boxes(img_path, draft_boxes):
    draft_boxes[img_path] = clone_boxes(boxes)


def remove_draft(img_path, draft_boxes):
    if img_path in draft_boxes:
        del draft_boxes[img_path]


def update_current_image(entry, img_path, lbl_path):
    entry["img_path"] = img_path
    entry["lbl_path"] = lbl_path


def delete_current_entry(entry):
    img_path = entry["img_path"]
    lbl_path = entry["lbl_path"]

    if os.path.exists(img_path):
        os.remove(img_path)
    if lbl_path and os.path.exists(lbl_path):
        os.remove(lbl_path)


def previous_index(idx):
    if idx <= 0:
        return 0
    return idx - 1


def next_index(idx):
    return idx + 1


def select_current_box_class():
    global current_class
    if selected_box_idx < 0 or selected_box_idx >= len(boxes):
        return False
    current_class = boxes[selected_box_idx][0]
    return True


def move_selected_box(dx, dy):
    if selected_box_idx < 0 or selected_box_idx >= len(boxes):
        return False
    cls, x1, y1, x2, y2 = boxes[selected_box_idx]
    moved = clamp_box((cls, x1 + dx, y1 + dy, x2 + dx, y2 + dy))
    if moved[3] - moved[1] <= 5 or moved[4] - moved[2] <= 5:
        return False
    boxes[selected_box_idx] = moved
    return True


def scale_selected_box(scale_delta):
    if selected_box_idx < 0 or selected_box_idx >= len(boxes):
        return False
    cls, x1, y1, x2, y2 = boxes[selected_box_idx]
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    half_w = max(3.0, (x2 - x1) * (1.0 + scale_delta) / 2.0)
    half_h = max(3.0, (y2 - y1) * (1.0 + scale_delta) / 2.0)
    scaled = clamp_box((
        cls,
        int(round(cx - half_w)),
        int(round(cy - half_h)),
        int(round(cx + half_w)),
        int(round(cy + half_h)),
    ))
    if scaled[3] - scaled[1] <= 5 or scaled[4] - scaled[2] <= 5:
        return False
    boxes[selected_box_idx] = scaled
    return True


def auto_label_current_image():
    global boxes, current_class
    if auto_labeler is None:
        safe_print("    [自动打标] 未启用，请传 --predict-model")
        return False

    predicted_boxes = auto_labeler.predict_boxes(img_orig)
    boxes.clear()
    boxes.extend(predicted_boxes)
    if boxes:
        set_selected_box(len(boxes) - 1)
        current_class = boxes[selected_box_idx][0]
        safe_print(f"    [自动打标] 生成 {len(boxes)} 个框")
    else:
        set_selected_box(-1)
        safe_print("    [自动打标] 未检测到可用框")
    draw_overlay()
    cv2.imshow("YOLO Label Tool", img_display)
    return bool(boxes)


def draw_overlay():
    global img_display
    img_display = img_orig.copy()
    h, _w = img_display.shape[:2]
    legend_y = 20

    for idx, (cls, x1, y1, x2, y2) in enumerate(boxes):
        color = CLASS_COLORS.get(cls, (128, 128, 128))
        label = OVERLAY_NAMES.get(cls, CLASS_NAMES.get(cls, "?"))
        thickness = 3 if idx == selected_box_idx else 2
        cv2.rectangle(img_display, (x1, y1), (x2, y2), color, thickness)
        if idx == selected_box_idx:
            cv2.drawMarker(
                img_display,
                ((x1 + x2) // 2, (y1 + y2) // 2),
                (0, 255, 255),
                markerType=cv2.MARKER_CROSS,
                markerSize=12,
                thickness=2,
            )
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
        (
            f"class: {OVERLAY_NAMES.get(current_class, '?')} | "
            f"boxes: {len(boxes)} | "
            f"selected: {selected_box_idx if selected_box_idx >= 0 else '-'} | "
            f"hits: {len(selection_hit_indices) if selection_hit_indices else '-'} | "
            f"{short_help()}"
        ),
        (5, h - 10),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (0, 255, 255),
        1,
    )


def mouse_cb(event, x, y, flags, param):
    global drawing, ix, iy, current_class
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
            new_box = (current_class, x1, y1, x2, y2)
            if not replace_selected_box(new_box):
                boxes.append(new_box)
                set_selected_box(len(boxes) - 1)
            reset_hit_selection_cycle()
            draw_overlay()
            cv2.imshow("YOLO Label Tool", img_display)
    elif event == cv2.EVENT_RBUTTONUP:
        selected_cls, hit_pos, hit_count = select_box_at(x, y)
        if selected_cls is not None:
            current_class = selected_cls
            if hit_count > 1:
                safe_print(
                    f"    [选中] 重叠框 {hit_pos}/{hit_count}: "
                    f"{DISPLAY_NAMES.get(selected_cls, '?')} ({CLASS_NAMES.get(selected_cls, '?')})"
                )
        draw_overlay()
        cv2.imshow("YOLO Label Tool", img_display)


def label_loop(file_pairs, save_func, mode_name):
    global current_class, boxes, img_orig

    cv2.namedWindow("YOLO Label Tool", cv2.WINDOW_NORMAL)
    cv2.setMouseCallback("YOLO Label Tool", mouse_cb)
    print_help()

    labeled = 0
    draft_boxes = {}
    idx = 0
    while 0 <= idx < len(file_pairs):
        entry = file_pairs[idx]
        img_path = entry["img_path"]
        lbl_path = entry["lbl_path"]
        preserved_class = current_class
        img_orig = cv2.imread(img_path)
        if img_orig is None:
            safe_print(f"[跳过] 无法读取图片: {img_path}")
            idx = next_index(idx)
            continue
        h, w = img_orig.shape[:2]
        boxes = get_initial_boxes(img_path, lbl_path, w, h, draft_boxes)
        clear_selected_box()

        if auto_predict_enabled and not boxes:
            auto_label_current_image()
            clear_selected_box()

        current_class = preserved_class

        cv2.resizeWindow("YOLO Label Tool", min(w, 1280), int(h * min(w, 1280) / w))
        draw_overlay()
        cv2.imshow("YOLO Label Tool", img_display)

        safe_print(f"[{idx + 1}/{len(file_pairs)}] {os.path.basename(img_path)} ({w}x{h})")
        while True:
            key = cv2.waitKey(0) & 0xFF
            lower_key = ord(chr(key).lower()) if key < 256 else key

            if lower_key in KEY_TO_CLASS:
                current_class = KEY_TO_CLASS[lower_key]
                sync_selection_for_target_class(current_class)
                safe_print(
                    f"    类别 -> {DISPLAY_NAMES.get(current_class)} "
                    f"({CLASS_NAMES.get(current_class)})"
                )
                draw_overlay()
                cv2.imshow("YOLO Label Tool", img_display)
            elif key in (ord("n"), ord("N")):
                current_class = (current_class + 1) % len(CLASS_NAMES)
                sync_selection_for_target_class(current_class)
                safe_print(
                    f"    类别 -> {DISPLAY_NAMES.get(current_class)} "
                    f"({CLASS_NAMES.get(current_class)})"
                )
                draw_overlay()
                cv2.imshow("YOLO Label Tool", img_display)
            elif key in (ord("m"), ord("M")):
                current_class = (current_class - 1) % len(CLASS_NAMES)
                sync_selection_for_target_class(current_class)
                safe_print(
                    f"    类别 -> {DISPLAY_NAMES.get(current_class)} "
                    f"({CLASS_NAMES.get(current_class)})"
                )
                draw_overlay()
                cv2.imshow("YOLO Label Tool", img_display)
            elif key in (ord("z"), ord("Z")):
                if boxes:
                    removed = boxes.pop()
                    reset_hit_selection_cycle()
                    set_selected_box(len(boxes) - 1 if boxes else -1)
                    select_current_box_class()
                    safe_print(f"    撤销: {DISPLAY_NAMES.get(removed[0], '?')}")
                    draw_overlay()
                    cv2.imshow("YOLO Label Tool", img_display)
            elif key in (ord("x"), ord("X")):
                boxes.clear()
                reset_hit_selection_cycle()
                set_selected_box(-1)
                safe_print("    已清空当前图片全部标注")
                draw_overlay()
                cv2.imshow("YOLO Label Tool", img_display)
            elif key == KEY_BACKSPACE:
                if selected_box_idx >= 0 and selected_box_idx < len(boxes):
                    removed = boxes.pop(selected_box_idx)
                    reset_hit_selection_cycle()
                    set_selected_box(-1)
                    safe_print(f"    删除选中框: {DISPLAY_NAMES.get(removed[0], '?')}")
                    draw_overlay()
                    cv2.imshow("YOLO Label Tool", img_display)
            elif key in (ord("h"), ord("H")):
                print_help()
            elif key in (ord("a"), ord("A")):
                auto_label_current_image()
            elif key == ord("["):
                if scale_selected_box(-BOX_RESIZE_STEP):
                    draw_overlay()
                    cv2.imshow("YOLO Label Tool", img_display)
            elif key == ord("]"):
                if scale_selected_box(BOX_RESIZE_STEP):
                    draw_overlay()
                    cv2.imshow("YOLO Label Tool", img_display)
            elif key == ord(","):
                if move_selected_box(0, -BOX_MOVE_STEP):
                    draw_overlay()
                    cv2.imshow("YOLO Label Tool", img_display)
            elif key == ord("."):
                if move_selected_box(0, BOX_MOVE_STEP):
                    draw_overlay()
                    cv2.imshow("YOLO Label Tool", img_display)
            elif key == ord(";"):
                if move_selected_box(-BOX_MOVE_STEP, 0):
                    draw_overlay()
                    cv2.imshow("YOLO Label Tool", img_display)
            elif key == ord("'"):
                if move_selected_box(BOX_MOVE_STEP, 0):
                    draw_overlay()
                    cv2.imshow("YOLO Label Tool", img_display)
            elif key in (ord("s"), ord("S"), 13):
                if not boxes:
                    safe_print("    [跳过] 没有标注框")
                    break
                break
            elif key in (ord("d"), ord("D"), ord("=")):
                stash_current_boxes(img_path, draft_boxes)
                safe_print("    [跳过] 此图不修改")
                break
            elif key == KEY_CTRL_D:
                safe_print("    [删除] 当前图片将被删除并跳到下一张")
                break
            elif key in (ord("j"), ord("J"), ord("-")):
                stash_current_boxes(img_path, draft_boxes)
                if idx == 0:
                    safe_print("    [提示] 已经是第一张")
                break
            elif key in (ord("q"), ord("Q"), 27):
                stash_current_boxes(img_path, draft_boxes)
                cv2.destroyAllWindows()
                safe_print(f"\n[退出] 共{mode_name} {labeled} 张")
                return

        if key in (ord("s"), ord("S"), 13):
            saved_img_path, saved_lbl_path = save_func(entry)
            remove_draft(img_path, draft_boxes)
            update_current_image(entry, saved_img_path, saved_lbl_path)
            draft_boxes[saved_img_path] = clone_boxes(boxes)
            labeled += 1
            safe_print(f"    [保存] {len(boxes)} 个框")
            idx = next_index(idx)
        elif key in (ord("d"), ord("D"), ord("=")):
            idx = next_index(idx)
        elif key == KEY_CTRL_D:
            remove_draft(img_path, draft_boxes)
            delete_current_entry(entry)
            file_pairs.pop(idx)
            if idx >= len(file_pairs):
                idx = len(file_pairs) - 1
        elif key in (ord("j"), ord("J"), ord("-")):
            idx = previous_index(idx)

    cv2.destroyAllWindows()
    safe_print(f"\n[完成] 共{mode_name} {labeled} 张")


def build_parser():
    parser = build_label_parser("YOLO 多颜色鱼标注工具")
    parser.add_argument("--base-dir", type=str, default="", help="数据目录（可选，默认使用配置路径）")
    parser.add_argument("--predict-model", type=str, default="", help="自动打标模型路径")
    parser.add_argument("--predict-conf", type=float, default=0.25, help="自动打标置信度阈值")
    parser.add_argument("--predict-device", type=str, default="auto", help="自动打标设备，如 auto/cpu/cuda")
    parser.add_argument("--auto-predict", action="store_true", help="打开图片时自动先跑一次模型预测")
    parser.add_argument("--multi-per-class", action="store_true", help="自动打标时允许同类多个框")
    return parser


def main(argv=None):
    global auto_labeler, auto_predict_enabled, TRAIN_IMG, TRAIN_LBL, UNLABELED, VAL_IMG, VAL_LBL
    parser = build_parser()
    args = parser.parse_args(argv)

    # 如果提供了自定义数据目录，更新 yolo.paths 中的全局路径
    if args.base_dir:
        base_dir = os.path.abspath(args.base_dir)
        yolo.paths.BASE = base_dir
        yolo.paths.UNLABELED = os.path.join(base_dir, "images", "unlabeled")
        yolo.paths.TRAIN_IMG = os.path.join(base_dir, "images", "train")
        yolo.paths.TRAIN_LBL = os.path.join(base_dir, "labels", "train")
        yolo.paths.VAL_IMG = os.path.join(base_dir, "images", "val")
        yolo.paths.VAL_LBL = os.path.join(base_dir, "labels", "val")
        # 更新本模块中的全局变量
        TRAIN_IMG = yolo.paths.TRAIN_IMG
        TRAIN_LBL = yolo.paths.TRAIN_LBL
        UNLABELED = yolo.paths.UNLABELED
        VAL_IMG = yolo.paths.VAL_IMG
        VAL_LBL = yolo.paths.VAL_LBL

    # 创建所需的目录
    for dir_path in [UNLABELED, TRAIN_IMG, TRAIN_LBL, VAL_IMG, VAL_LBL]:
        os.makedirs(dir_path, exist_ok=True)
    
    auto_predict_enabled = bool(args.auto_predict)

    if args.predict_model:
        auto_labeler = AutoLabeler(
            args.predict_model,
            conf=args.predict_conf,
            device=args.predict_device,
            one_per_class=not args.multi_per_class,
        )
        safe_print(
            f"[自动打标] 已启用: model={args.predict_model} "
            f"conf={args.predict_conf} device={args.predict_device} "
            f"{'one-per-class' if not args.multi_per_class else 'multi-per-class'}"
        )

    if args.relabel:
        items = list_relabel_entries(TRAIN_IMG, TRAIN_LBL, VAL_IMG, VAL_LBL)
        if not items:
            safe_print("[提示] train/ val/ 中没有可补标的图片")
            return

        def save_inplace(entry):
            lbl_path = entry["lbl_path"]
            write_yolo_labels(lbl_path, img_orig.shape, boxes)
            return entry["img_path"], lbl_path

        label_loop(items, save_inplace, mode_name="补标")
        return

    file_pairs = list_unlabeled_entries(UNLABELED)
    if not file_pairs:
        safe_print(f"[提示] {UNLABELED} 中没有未标注图片")
        return

    def save_new(entry):
        return save_new_labeled_entry(
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

    if args.auto_predict and auto_labeler is None:
        parser.error("--auto-predict 需要配合 --predict-model 使用")

    label_loop(file_pairs, save_new, mode_name="标注")


if __name__ == "__main__":
    main()
