"""
已标注数据导出工具
==================
将 train/val 中已有图片和对应标签打包为 zip，便于分发给他人。
"""

import os
import time
import zipfile

from fish_trainer.paths import TRAIN_IMG, TRAIN_LBL, VAL_IMG, VAL_LBL, ensure_dataset_dirs


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp")


def iter_labeled_pairs():
    ensure_dataset_dirs()
    for split, img_dir, lbl_dir in (
        ("train", TRAIN_IMG, TRAIN_LBL),
        ("val", VAL_IMG, VAL_LBL),
    ):
        if not os.path.isdir(img_dir):
            continue
        for name in sorted(os.listdir(img_dir)):
            if not name.lower().endswith(IMAGE_EXTS):
                continue
            img_path = os.path.join(img_dir, name)
            lbl_path = os.path.join(lbl_dir, os.path.splitext(name)[0] + ".txt")
            if os.path.exists(lbl_path):
                yield split, img_path, lbl_path


def build_export_name():
    return f"fish_trainer_labels_{time.strftime('%Y%m%d_%H%M%S')}.zip"


def export_labeled_dataset(zip_path):
    pairs = list(iter_labeled_pairs())
    if not pairs:
        raise ValueError("当前没有可导出的已标注图片和对应标签")

    root_dir = os.path.splitext(os.path.basename(zip_path))[0]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for split, img_path, lbl_path in pairs:
            img_name = os.path.basename(img_path)
            lbl_name = os.path.basename(lbl_path)
            zf.write(img_path, arcname=os.path.join(root_dir, "images", split, img_name))
            zf.write(lbl_path, arcname=os.path.join(root_dir, "labels", split, lbl_name))
    return len(pairs)


def get_dataset_stats():
    ensure_dataset_dirs()
    stats = {
        "train_images": 0,
        "train_labels": 0,
        "val_images": 0,
        "val_labels": 0,
        "labeled_pairs": 0,
        "unlabeled_images": 0,
    }

    if os.path.isdir(TRAIN_IMG):
        stats["train_images"] = sum(
            1 for name in os.listdir(TRAIN_IMG) if name.lower().endswith(IMAGE_EXTS)
        )
    if os.path.isdir(TRAIN_LBL):
        stats["train_labels"] = sum(
            1 for name in os.listdir(TRAIN_LBL) if name.lower().endswith(".txt")
        )
    if os.path.isdir(VAL_IMG):
        stats["val_images"] = sum(
            1 for name in os.listdir(VAL_IMG) if name.lower().endswith(IMAGE_EXTS)
        )
    if os.path.isdir(VAL_LBL):
        stats["val_labels"] = sum(
            1 for name in os.listdir(VAL_LBL) if name.lower().endswith(".txt")
        )

    stats["labeled_pairs"] = sum(1 for _ in iter_labeled_pairs())

    from fish_trainer.paths import UNLABELED

    if os.path.isdir(UNLABELED):
        stats["unlabeled_images"] = sum(
            1 for name in os.listdir(UNLABELED) if name.lower().endswith(IMAGE_EXTS)
        )
    return stats
