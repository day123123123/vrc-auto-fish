"""
YOLO 多颜色鱼模型训练脚本
==========================
保留 yolo 命令入口，但统一使用多颜色鱼类别体系。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yolo.console import safe_print
from yolo.paths import DATA_YAML, RUNS_DIR, TRAIN_IMG, VAL_IMG, ensure_dataset_dirs


def count_images(path):
    if not os.path.isdir(path):
        return 0
    return sum(
        1
        for f in os.listdir(path)
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp"))
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="YOLO 多颜色鱼模型训练")
    parser.add_argument("--model", type=str, default="yolov8n.pt", help="基础模型")
    parser.add_argument("--epochs", type=int, default=80, help="训练轮数")
    parser.add_argument("--imgsz", type=int, default=640, help="输入尺寸")
    parser.add_argument("--batch", type=int, default=-1, help="batch size")
    parser.add_argument("--resume", action="store_true", help="从上次中断继续训练")
    args = parser.parse_args(argv)

    ensure_dataset_dirs()
    n_train = count_images(TRAIN_IMG)
    n_val = count_images(VAL_IMG)

    safe_print("=" * 50)
    safe_print("  YOLO 多颜色鱼训练")
    safe_print("=" * 50)
    safe_print(f"  训练集: {n_train} 张")
    safe_print(f"  验证集: {n_val} 张")
    safe_print(f"  模型: {args.model}")
    safe_print(f"  数据配置: {DATA_YAML}")

    if n_train < 10:
        safe_print("[错误] 训练集图片不足，至少建议 10 张")
        return

    try:
        from ultralytics import YOLO
        import torch
    except ImportError:
        safe_print("[错误] 缺少 ultralytics 或 torch，请先安装依赖")
        return

    last_pt = os.path.join(RUNS_DIR, "fish_detect", "weights", "last.pt")
    if args.resume and os.path.exists(last_pt):
        model = YOLO(last_pt)
        safe_print(f"[继续训练] {last_pt}")
    else:
        model = YOLO(args.model)

    device = 0 if torch.cuda.is_available() else "cpu"
    model.train(
        data=DATA_YAML,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        project=RUNS_DIR,
        name="fish_detect",
        exist_ok=True,
        device=device,
        workers=4,
        patience=20,
        save=True,
        save_period=10,
        plots=True,
        verbose=True,
    )

    best_pt = os.path.join(RUNS_DIR, "fish_detect", "weights", "best.pt")
    if os.path.exists(best_pt):
        safe_print()
        safe_print("=" * 50)
        safe_print(f"  [OK] 训练完成: {best_pt}")
        safe_print("=" * 50)
    else:
        safe_print("[警告] 未找到 best.pt，请检查训练日志")


if __name__ == "__main__":
    main()
