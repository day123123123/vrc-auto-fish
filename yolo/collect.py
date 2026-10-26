"""
YOLO 多颜色鱼训练数据采集
==========================
保留 yolo 命令入口，但采集实现共享给 trainer_common。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trainer_common.collect import run_collect
from trainer_common.profiles import get_profile, TrainerProfile, CustomPathProfile
from yolo.console import safe_print


def build_parser():
    from trainer_common.collect import build_parser
    parser = build_parser(get_profile("runtime_yolo"))
    # 添加 --base-dir 参数支持
    parser.add_argument("--base-dir", type=str, default="", help="数据目录（可选，默认使用配置路径）")
    return parser


def main(argv=None):
    import argparse
    parser = build_parser()
    args, remaining = parser.parse_known_args(argv)
    
    profile = get_profile("runtime_yolo")
    
    # 如果提供了自定义数据目录，创建自定义 profile
    if args.base_dir:
        base_dir = os.path.abspath(args.base_dir)
        profile = CustomPathProfile(profile, custom_dataset_root=base_dir)
    
    # 构建过滤后的 argv（移除 --base-dir 参数），用于 trainer_common.collect
    new_argv = []
    skip_next = False
    for arg in (argv or []):
        if skip_next:
            skip_next = False
            continue
        if arg == "--base-dir":
            skip_next = True
            continue
        if arg.startswith("--base-dir="):
            continue
        new_argv.append(arg)
    
    run_collect(profile, safe_print, argv=new_argv)


if __name__ == "__main__":
    main()
