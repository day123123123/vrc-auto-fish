# VRChat 自动钓鱼助手 (FISH!)

VRChat 世界 **FISH!** 的自动钓鱼脚本。支持 YOLO 目标检测 + PD 控制器，全自动抛竿、提竿、小游戏操控。

## 功能

- **自动抛竿 / 提竿** — 检测咬钩动画，自动完成钓鱼流程
- **小游戏自动控制** — PD 控制器追踪鱼的位置，自动操控白条
- **YOLO 目标检测** — 训练后可替代模板匹配，准确率更高
- **GUI 界面** — 参数可视化调节，实时调试窗口
- **热键控制** — F9 开始/暂停，F10 停止，F11 调试模式
- **VRChat OSC 输入** — 可选 OSC 输入方式，不占用鼠标

## 快速开始

### 方式一：一键启动 (推荐)

1. 安装 [Python 3.10+](https://www.python.org/downloads/)（安装时勾选 **Add to PATH**）
2. 双击 **`启动.bat`** — 首次自动安装依赖，之后直接启动

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
2. 运行程序，点击「选择窗口」绑定 VRChat 窗口
3. 点击「框选区域」选择钓鱼小游戏的检测范围（可选）
4. 按 **F9** 开始自动钓鱼

## 快捷键

| 按键 | 功能 |
|------|------|
| F9   | 开始 / 暂停 |
| F10  | 停止 |
| F11  | 调试模式 (显示检测窗口) |

## 项目结构

```
├── main.py          # 入口
├── config.py        # 全局配置
├── core/            # 核心逻辑
│   ├── bot.py       # 钓鱼主循环 + PD控制器
│   ├── detector.py  # 模板匹配检测
│   ├── yolo_detector.py  # YOLO 检测
│   ├── screen.py    # 截屏
│   ├── window.py    # 窗口管理
│   └── input_ctrl.py     # 输入控制
├── gui/             # GUI 界面
│   └── app.py
├── utils/           # 工具
│   └── logger.py
├── img/             # 模板图片
├── yolo/            # YOLO 模型 & 训练工具
├── 启动.bat          # 一键启动 (自动安装+运行)
├── install.bat      # 单独安装依赖
└── start.bat        # 单独启动程序
```

## YOLO 模型训练

如果需要自行训练 / 重新训练 YOLO 检测模型（提升准确率或适配新分辨率），按以下步骤操作。

### 1. 采集数据

启动 VRChat 进入 FISH! 世界后，运行采集工具。工具会在后台持续截图，你只需正常钓鱼即可。

```bash
python -m yolo.collect               # 默认每秒 2 帧
python -m yolo.collect --fps 4       # 每秒 4 帧 (更多数据)
python -m yolo.collect --roi         # 仅截取 ROI 区域
python -m yolo.collect --max 500     # 最多截 500 张后自动停止
```

截图保存在 `yolo/dataset/images/unlabeled/`，按 `Ctrl+C` 停止。

### 2. 标注数据

用内置标注工具画框并分配类别：

```bash
python -m yolo.label                 # 标注 unlabeled/ 中的新图
python -m yolo.label --split 0.2     # 20% 自动分到验证集 (默认)
python -m yolo.label --relabel       # 补标已有图片 (如新增 progress 类别)
```

**标注操作：**

| 按键 | 功能 |
|------|------|
| 鼠标拖拽 | 画框 |
| 1 | 选择类别 fish (鱼图标) |
| 2 | 选择类别 bar (白色捕捉条) |
| 3 | 选择类别 track (钓鱼轨道) |
| 4 | 选择类别 progress (绿色进度条) |
| Z | 撤销上一个框 |
| S / Enter | 保存并下一张 |
| D | 跳过当前图片 |
| Q / Esc | 退出 |

### 3. 训练模型

```bash
python -m yolo.train                 # 默认 yolov8n, 50 轮
python -m yolo.train --epochs 100    # 训练 100 轮
python -m yolo.train --model yolov8s.pt  # 使用更大模型 (精度↑ 速度↓)
python -m yolo.train --resume        # 从上次中断处继续训练
python -m yolo.train --batch 8       # 手动指定批大小
```

训练完成后，最佳模型保存在 `yolo/runs/fish_detect/weights/best.pt`，程序启动时会自动加载。

### 数据集目录结构

```
yolo/dataset/
├── data.yaml              # 数据集配置 (类别名、路径)
├── images/
│   ├── unlabeled/         # 采集的原始截图 (待标注)
│   ├── train/             # 训练集图片
│   └── val/               # 验证集图片
└── labels/
    ├── train/             # 训练集标注 (YOLO 格式 .txt)
    └── val/               # 验证集标注
```

## 更新补丁

如果使用 EXE 版本，下载补丁 zip 后解压到 EXE 同级目录，确保生成 `patch/` 文件夹即可。程序启动时会自动加载补丁。

## License

MIT
