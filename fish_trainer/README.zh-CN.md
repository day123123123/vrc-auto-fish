[简体中文](README.zh-CN.md) | [English](README.en-US.md) | [日本語](README.ja-JP.md)

# Fish Trainer

面向独立多颜色鱼数据链路的训练工具箱。`fish_trainer/` 和 `yolo/` 现在共享 `trainer_common/` 的采集、标注、训练底层实现，但使用不同的 profile、数据目录和训练输出目录。

## 定位

- 维护独立的 `multicolor` 数据链路，不直接覆盖主程序运行时的 `yolo/` 数据
- 支持采集、手工标注、补标、迁移旧数据、训练、导出和 GUI 管理
- 保留 `fish_generic`，兼容旧 `fish` 单类标注

## 数据目录结构

`fish_trainer` profile 默认使用这些目录：

```text
fish_trainer/
├── dataset/
│   ├── images/
│   │   ├── unlabeled/
│   │   ├── train/
│   │   └── val/
│   ├── labels/
│   │   ├── train/
│   │   └── val/
│   └── data_multiclass.yaml
└── runs/
    └── fish_multiclass/
```

流程固定是：

```text
collect -> images/unlabeled -> label -> train/val -> train -> runs/fish_multiclass
```

## 类别与快捷键

- `fish_generic`
- `fish_white`
- `fish_copper`
- `fish_green`
- `fish_blue`
- `fish_purple`
- `fish_golden`
- `fish_red`
- `fish_pink`
- `fish_rainbow`
- `bar`
- `track`
- `progress`
- `fish_teal`

- `F`: 通用鱼
- `1-9`: 白 / 铜 / 绿 / 蓝 / 紫 / 金 / 红 / 粉 / 彩
- `B`: `bar`
- `T`: `track`
- `P`: `progress`
- `0`: `fish_teal`
- `N` / `M`: 下一个 / 上一个类别
- `Z`: 撤销
- `X`: 清空当前图片全部标注
- `H`: 再次打印帮助
- `S` / `Enter`: 保存
- `D`: 跳过
- `Q` / `Esc`: 退出

## 采集

从 VRChat 窗口截图，保存到 `fish_trainer/dataset/images/unlabeled/`。

常用命令：

```powershell
python -m fish_trainer.collect --fps 2 --roi
python -m fish_trainer.collect --fps 2 --roi --max 200
```

参数说明：

- `--fps`：每秒截图数，默认 `2.0`
- `--roi`：只截取已保存的 ROI
- `--max`：最大截图数量，`0` 表示不限

## 新图标注

对 `images/unlabeled/` 里的新图进行标注。保存时会按照 `--split` 随机移动到 `train/` 或 `val/`，同时写入对应标签文件。

常用命令：

```powershell
python -m fish_trainer.label --split 0.2
```

参数说明：

- `--split`：验证集比例，默认 `0.2`

## 补标

对已经进入 `train/` / `val/` 的数据原地重写标签，不移动图片。

```powershell
python -m fish_trainer.label --relabel
```

## 迁移旧数据

把旧 `yolo/dataset` 中的单类 `fish` 数据迁移到 `fish_trainer/dataset`。

常用命令：

```powershell
python -m fish_trainer.migrate_labels
python -m fish_trainer.migrate_labels --with-unlabeled
python -m fish_trainer.migrate_labels --source D:\old\yolo\dataset --overwrite
```

参数说明：

- `--source`：旧数据集目录，默认 `yolo/dataset`
- `--overwrite`：覆盖目标目录里已有文件
- `--with-unlabeled`：同时复制旧 `images/unlabeled`

当前默认映射：

- `fish -> fish_generic`
- `bar -> bar`
- `track -> track`
- `progress -> progress`

## 训练

训练输出默认写到 `fish_trainer/runs/fish_multiclass/weights/`。

常用命令：

```powershell
python -m fish_trainer.train --model yolov8n.pt --epochs 80 --imgsz 640 --batch -1
python -m fish_trainer.train --resume
```

参数说明：

- `--model`：基础模型，默认 `yolov8n.pt`
- `--epochs`：训练轮数，默认 `80`
- `--imgsz`：输入尺寸，默认 `640`
- `--batch`：batch size，默认 `-1`
- `--resume`：从 `last.pt` 继续训练

## GUI 入口

如果你更习惯桌面化流程，可以直接启动 GUI：

```powershell
python -m fish_trainer.gui
```

GUI 里可以：

- 启动 / 停止采集
- 启动新图标注或补标
- 查看 `unlabeled/train/val` 统计
- 导出已标注图片和标签 zip
- 打开数据目录

## 关于 `fish_teal` 的说明

当前标注器已经支持 `fish_teal`，快捷键是 `0`。但训练配置文件 `fish_trainer/dataset/data_multiclass.yaml` 是否已同步声明该类，需要你在开始正式训练前再核对一次；本文档优先描述当前工具实际支持的标注能力。

