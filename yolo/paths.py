"""
YOLO 工具路径
=============
保留 yolo 目录结构，但统一使用多颜色鱼类别体系。
"""

import os
import sys

import config


APP_ROOT = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else config.BASE_DIR
)
BASE = os.path.join(APP_ROOT, "yolo", "dataset")
UNLABELED = os.path.join(BASE, "images", "unlabeled")
TRAIN_IMG = os.path.join(BASE, "images", "train")
TRAIN_LBL = os.path.join(BASE, "labels", "train")
VAL_IMG = os.path.join(BASE, "images", "val")
VAL_LBL = os.path.join(BASE, "labels", "val")
DATA_YAML = os.path.join(BASE, "data.yaml")
RUNS_DIR = os.path.join(APP_ROOT, "yolo", "runs")


def ensure_dataset_dirs():
    for path in (UNLABELED, TRAIN_IMG, TRAIN_LBL, VAL_IMG, VAL_LBL):
        os.makedirs(path, exist_ok=True)
