"""
训练工具 profile 定义
====================
使用 profile 统一不同训练链路的目录与命名约定。
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import sys

import config


@dataclass(frozen=True)
class TrainerProfile:
    name: str
    dataset_dir: str
    runs_dir: str
    data_yaml_name: str
    train_run_name: str
    collect_description: str
    train_description: str
    train_banner: str

    @property
    def app_root(self) -> str:
        return (
            os.path.dirname(sys.executable)
            if getattr(sys, "frozen", False)
            else config.BASE_DIR
        )

    @property
    def dataset_root(self) -> str:
        return os.path.join(self.app_root, self.dataset_dir)

    @property
    def runs_root(self) -> str:
        return os.path.join(self.app_root, self.runs_dir)

    @property
    def data_yaml(self) -> str:
        return os.path.join(self.dataset_root, self.data_yaml_name)


PROFILES: dict[str, TrainerProfile] = {
    "runtime_yolo": TrainerProfile(
        name="runtime_yolo",
        dataset_dir=os.path.join("yolo", "dataset"),
        runs_dir=os.path.join("yolo", "runs"),
        data_yaml_name="data.yaml",
        train_run_name="fish_detect",
        collect_description="YOLO 多颜色鱼训练数据采集",
        train_description="YOLO 多颜色鱼模型训练",
        train_banner="YOLO 多颜色鱼训练",
    ),
    "multicolor": TrainerProfile(
        name="multicolor",
        dataset_dir=os.path.join("fish_trainer", "dataset"),
        runs_dir=os.path.join("fish_trainer", "runs"),
        data_yaml_name="data_multiclass.yaml",
        train_run_name="fish_multiclass",
        collect_description="多颜色鱼训练数据采集",
        train_description="多颜色鱼模型训练",
        train_banner="多颜色鱼 YOLO 训练",
    ),
}


def get_profile(name: str) -> TrainerProfile:
    return PROFILES[name]


class CustomPathProfile:
    """包装 TrainerProfile，支持自定义数据目录"""
    def __init__(self, base_profile: TrainerProfile, custom_dataset_root: str | None = None):
        self._base_profile = base_profile
        self._custom_dataset_root = custom_dataset_root
    
    def __getattr__(self, name):
        """代理所有属性访问到 base_profile"""
        return getattr(self._base_profile, name)
    
    @property
    def dataset_root(self) -> str:
        """返回自定义的数据目录或默认值"""
        if self._custom_dataset_root:
            return self._custom_dataset_root
        return self._base_profile.dataset_root

    @property
    def runs_root(self) -> str:
        """返回自定义的运行结果目录或默认值"""
        if self._custom_dataset_root:
            # 默认将 runs 放在自定义数据目录下
            return os.path.join(self._custom_dataset_root, "runs")
        return self._base_profile.runs_root

    @property
    def data_yaml(self) -> str:
        """返回自定义的 data.yaml 路径"""
        return os.path.join(self.dataset_root, self._base_profile.data_yaml_name)
