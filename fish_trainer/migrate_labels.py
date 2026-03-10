"""
旧标注迁移工具
==============
把旧 `yolo/dataset` 的单类 fish 标注迁移到 `fish_trainer/dataset`。
"""

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from fish_trainer.paths import TRAIN_IMG, TRAIN_LBL, UNLABELED, VAL_IMG, VAL_LBL, ensure_dataset_dirs

LEGACY_DATASET = os.path.join(config.BASE_DIR, "yolo", "dataset")
OLD_TO_NEW_CLASS = {
    0: 0,    # fish -> fish_generic
    1: 10,   # bar
    2: 11,   # track
    3: 12,   # progress
}


def ensure_parent(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)


def migrate_label_file(src_path, dst_path):
    converted = 0
    skipped = 0
    ensure_parent(dst_path)
    with open(src_path, "r", encoding="utf-8") as src, open(dst_path, "w", encoding="utf-8") as dst:
        for line in src:
            parts = line.strip().split()
            if len(parts) < 5:
                skipped += 1
                continue
            old_cls = int(parts[0])
            if old_cls not in OLD_TO_NEW_CLASS:
                skipped += 1
                continue
            new_cls = OLD_TO_NEW_CLASS[old_cls]
            dst.write(f"{new_cls} {' '.join(parts[1:])}\n")
            converted += 1
    return converted, skipped


def copy_tree_split(source_root, split_name, overwrite=False):
    src_img_dir = os.path.join(source_root, "images", split_name)
    src_lbl_dir = os.path.join(source_root, "labels", split_name)
    dst_img_dir = TRAIN_IMG if split_name == "train" else VAL_IMG
    dst_lbl_dir = TRAIN_LBL if split_name == "train" else VAL_LBL

    stats = {"images": 0, "labels": 0, "skipped": 0}
    if not os.path.isdir(src_img_dir):
        return stats

    for name in sorted(os.listdir(src_img_dir)):
        if not name.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
            continue
        src_img = os.path.join(src_img_dir, name)
        dst_img = os.path.join(dst_img_dir, name)
        lbl_name = os.path.splitext(name)[0] + ".txt"
        src_lbl = os.path.join(src_lbl_dir, lbl_name)
        dst_lbl = os.path.join(dst_lbl_dir, lbl_name)

        if not overwrite and os.path.exists(dst_img):
            stats["skipped"] += 1
            continue

        ensure_parent(dst_img)
        shutil.copy2(src_img, dst_img)
        stats["images"] += 1

        if os.path.exists(src_lbl):
            converted, _ignored = migrate_label_file(src_lbl, dst_lbl)
            if converted > 0:
                stats["labels"] += 1
    return stats


def copy_unlabeled(source_root, overwrite=False):
    src_dir = os.path.join(source_root, "images", "unlabeled")
    copied = 0
    if not os.path.isdir(src_dir):
        return copied
    for name in sorted(os.listdir(src_dir)):
        if not name.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
            continue
        src = os.path.join(src_dir, name)
        dst = os.path.join(UNLABELED, name)
        if not overwrite and os.path.exists(dst):
            continue
        ensure_parent(dst)
        shutil.copy2(src, dst)
        copied += 1
    return copied


def main():
    parser = argparse.ArgumentParser(description="迁移旧 yolo 标注到 fish_trainer")
    parser.add_argument("--source", default=LEGACY_DATASET, help="旧数据集目录")
    parser.add_argument("--overwrite", action="store_true", help="覆盖目标目录中已有文件")
    parser.add_argument("--with-unlabeled", action="store_true", help="同时复制 unlabeled 图片")
    args = parser.parse_args()

    source_root = args.source
    ensure_dataset_dirs()
    print(f"[迁移] 来源: {source_root}")
    print(f"[迁移] 目标: {os.path.join(config.BASE_DIR, 'fish_trainer', 'dataset')}")

    train_stats = copy_tree_split(source_root, "train", overwrite=args.overwrite)
    val_stats = copy_tree_split(source_root, "val", overwrite=args.overwrite)
    unlabeled_count = (
        copy_unlabeled(source_root, overwrite=args.overwrite)
        if args.with_unlabeled else 0
    )

    print("[完成] 迁移结果:")
    print(f"  train: 图片 {train_stats['images']} 张, 标签 {train_stats['labels']} 个, 跳过 {train_stats['skipped']}")
    print(f"  val:   图片 {val_stats['images']} 张, 标签 {val_stats['labels']} 个, 跳过 {val_stats['skipped']}")
    if args.with_unlabeled:
        print(f"  unlabeled: 复制 {unlabeled_count} 张")
    print("  旧类别映射: fish->fish_generic, bar->bar, track->track, progress->progress")


if __name__ == "__main__":
    main()
