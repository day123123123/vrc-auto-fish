"""
训练工具路径
============
集中维护独立训练工具使用的数据集和输出目录。
"""

import os

import config


BASE = os.path.join(config.BASE_DIR, "fish_trainer", "dataset")
UNLABELED = os.path.join(BASE, "images", "unlabeled")
TRAIN_IMG = os.path.join(BASE, "images", "train")
TRAIN_LBL = os.path.join(BASE, "labels", "train")
VAL_IMG = os.path.join(BASE, "images", "val")
VAL_LBL = os.path.join(BASE, "labels", "val")
DATA_YAML = os.path.join(BASE, "data_multiclass.yaml")
RUNS_DIR = os.path.join(config.BASE_DIR, "fish_trainer", "runs")


def ensure_dataset_dirs():
    for path in (UNLABELED, TRAIN_IMG, TRAIN_LBL, VAL_IMG, VAL_LBL):
        os.makedirs(path, exist_ok=True)
