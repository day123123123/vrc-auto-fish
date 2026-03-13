[简体中文](README.zh-CN.md) | [English](README.en-US.md) | [日本語](README.ja-JP.md)

# VRChat 自动钓鱼助手 (FISH!)

VRChat 世界 **FISH!** 的自动钓鱼脚本。支持 YOLO 目标检测 + PD 控制器，全自动抛竿、提竿、小游戏操控。

## 功能

- **自动抛竿 / 提竿**: 检测咬钩动画，自动完成钓鱼流程
- **小游戏自动控制**: PD 控制器追踪鱼的位置，自动操控白条
- **YOLO 目标检测**: 训练后可替代模板匹配，准确率更高
- **GUI 界面**: 参数可视化调节，实时调试窗口
- **热键控制**: `F9` 开始/暂停，`F10` 停止，`F11` 调试模式
- **VRChat OSC 输入**: 可选 OSC 输入方式，不占用鼠标

## 快速开始

### 方式一：一键启动（推荐）

1. 安装 [Python 3.10+](https://www.python.org/downloads/)（安装时勾选 **Add to PATH**）
2. 双击 `启动.bat`，首次自动安装依赖，之后直接启动

> 自动检测显卡：NVIDIA GPU 安装 CUDA 加速版，AMD / Intel 安装 CPU 版，都能用。

### 方式二：手动安装

```bash
# 安装 PyTorch (GPU 版)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

# 或 CPU 版
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# 安装其他依赖
pip install -r requirements.txt

# 启动
python main.py
```

## 使用说明

1. 先启动 VRChat 并进入 FISH! 世界
2. 运行程序，点击“选择窗口”绑定 VRChat 窗口
3. 点击“框选区域”选择钓鱼小游戏的检测范围（可选）
4. 按 `F9` 开始自动钓鱼

## 快捷键

| 按键 | 功能 |
| --- | --- |
| `F9` | 开始 / 暂停 |
| `F10` | 停止 |
| `F11` | 调试模式（显示检测窗口） |

## 项目结构

```text
├── main.py              # 入口
├── config.py            # 全局配置
├── core/                # 核心逻辑
│   ├── bot.py           # 钓鱼主循环 + PD控制器
│   ├── detector.py      # 模板匹配检测
│   ├── yolo_detector.py # YOLO 检测
│   ├── screen.py        # 截屏
│   ├── window.py        # 窗口管理
│   └── input_ctrl.py    # 输入控制
├── gui/                 # GUI 界面
│   └── app.py
├── utils/               # 工具
│   └── logger.py
├── img/                 # 模板图片
├── yolo/                # 旧版 YOLO 模型与训练脚本
├── fish_trainer/        # 独立多颜色鱼采集 / 标注 / 迁移 / 训练工具
├── 启动.bat            # 一键启动（自动安装 + 运行）
├── install.bat          # 单独安装依赖
└── start.bat            # 单独启动程序
```

## YOLO 模型训练

如果你只是想重新训练当前主程序使用的旧版 YOLO 检测模型，可以继续使用 `yolo/` 下的旧脚本：

```bash
python -m yolo.collect
python -m yolo.label
python -m yolo.train
```

当前 `yolo.label` 已支持更顺手的补标流程：

- `python -m yolo.label --predict-model yolo\runs\fish_detect\weights\best.pt`: 用现有模型做自动预标
- `--auto-predict`: 打开图片时自动先跑一遍预测
- 鼠标右键选中已有框后，会自动切换到该框对应类别
- 选中框后直接左键重画，会覆盖旧框
- `J`: 回到上一张图片
- `Ctrl+D`: 删除当前图片文件并跳到下一张
- 翻页时不会自动选中框，但会保留当前激活类别

如果要补标已经进入 `train/` 或 `val/` 的图片：

```bash
python -m yolo.label --relabel
```

如果你要做“多颜色鱼”的独立采集 / 标注 / 迁移 / 训练，优先使用新的 `fish_trainer/` 工具链。

- 工具总说明: [`fish_trainer/README.zh-CN.md`](fish_trainer/README.zh-CN.md)
- 适用场景: 多颜色鱼类别、旧 `fish` 标注兼容迁移、独立训练流程
- 入口命令:

```bash
python -m fish_trainer.collect
python -m fish_trainer.label
python -m fish_trainer.migrate_labels
python -m fish_trainer.train
```

这样主 README 只保留总入口，具体快捷键、类别定义、迁移方式和训练参数请直接看 [`fish_trainer/README.zh-CN.md`](fish_trainer/README.zh-CN.md)。

## 更新补丁

如果使用 EXE 版本，下载补丁 zip 后解压到 EXE 同级目录，确保生成 `patch/` 文件夹即可。程序启动时会自动加载补丁。

## License

MIT
