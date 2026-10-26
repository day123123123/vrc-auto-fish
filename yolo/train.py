"""
YOLO 多颜色鱼模型训练脚本
==========================
保留 yolo 命令入口，但训练实现共享给 trainer_common。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trainer_common.profiles import get_profile, TrainerProfile, CustomPathProfile
from trainer_common.train import run_train
from yolo.console import safe_print


def count_images(path):
    from trainer_common.dataset import count_images as shared_count_images

    return shared_count_images(path)


def build_parser():
    from trainer_common.train import build_parser
    parser = build_parser(get_profile("runtime_yolo"))
    # 添加 --base-dir 参数支持
    parser.add_argument("--base-dir", type=str, default="", help="数据目录（可选，默认使用配置路径）")
    return parser


def main(argv=None):
    import argparse
    if argv is None:
        argv = sys.argv[1:]
    
    parser = build_parser()
    args, remaining = parser.parse_known_args(argv)
    
    profile = get_profile("runtime_yolo")
    
    # 如果提供了自定义数据目录，创建自定义 profile
    if args.base_dir:
        base_dir = os.path.abspath(args.base_dir)
        profile = CustomPathProfile(profile, custom_dataset_root=base_dir)
        
        # 确保数据目录存在
        os.makedirs(base_dir, exist_ok=True)
        
        # 确保 data.yaml 存在于自定义目录下，并更新其路径
        custom_yaml = profile.data_yaml
        default_yaml = os.path.join(get_profile("runtime_yolo").dataset_root, profile.data_yaml_name)
        
        if not os.path.exists(custom_yaml) and os.path.exists(default_yaml):
            import shutil
            safe_print(f"[提示] 拷贝默认 data.yaml 到 {custom_yaml}")
            shutil.copy2(default_yaml, custom_yaml)
        
        if os.path.exists(custom_yaml):
            # 无论是否是新拷贝的，都强制更新 yaml 中的 path 字段为当前 base_dir
            with open(custom_yaml, "r", encoding="utf-8") as f:
                lines = f.readlines()
            
            with open(custom_yaml, "w", encoding="utf-8") as f:
                path_updated = False
                for line in lines:
                    if line.strip().startswith("path:"):
                        f.write(f"path: {base_dir}\n")
                        path_updated = True
                    else:
                        f.write(line)
                if not path_updated:
                    # 如果没有 path 字段，在开头插入一个
                    f.seek(0, 0)
                    content = f"path: {base_dir}\n" + "".join(lines)
                    f.write(content)
    
    # 构建过滤后的 argv（移除 --base-dir 参数），用于 trainer_common.train
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
    
    run_train(profile, safe_print, argv=new_argv)


if __name__ == "__main__":
    main()
