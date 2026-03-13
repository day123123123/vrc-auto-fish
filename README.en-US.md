[简体中文](README.zh-CN.md) | [English](README.en-US.md) | [日本語](README.ja-JP.md)

# VRChat Auto Fishing Assistant (FISH!)

An auto-fishing script for the VRChat world **FISH!**. It supports YOLO object detection and a PD controller to automate casting, hooking, and the fishing mini-game.

## Features

- **Automatic casting / hooking**: Detects bite animations and completes the fishing loop automatically
- **Mini-game automation**: Uses a PD controller to track the fish position and control the white bar
- **YOLO object detection**: Can replace template matching after training for higher accuracy
- **GUI**: Visual parameter tuning with a real-time debug window
- **Hotkeys**: `F9` start/pause, `F10` stop, `F11` debug mode
- **VRChat OSC input**: Optional OSC-based input so the mouse is not occupied

## Quick Start

### Option 1: One-click launch (recommended)

1. Install [Python 3.10+](https://www.python.org/downloads/) and enable **Add to PATH**
2. Double-click `启动.bat`; dependencies are installed automatically on first run, then the app starts directly afterward

> GPU detection is automatic: NVIDIA installs the CUDA build, AMD / Intel installs the CPU build.

### Option 2: Manual installation

```bash
# Install PyTorch (GPU build)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

# Or CPU build
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Install remaining dependencies
pip install -r requirements.txt

# Start
python main.py
```

## Usage

1. Launch VRChat and enter the `FISH!` world
2. Run the program and click "Select Window" to bind the VRChat window
3. Click "Select Region" to define the fishing mini-game detection area if needed
4. Press `F9` to start auto fishing

## Hotkeys

| Key | Function |
| --- | --- |
| `F9` | Start / pause |
| `F10` | Stop |
| `F11` | Debug mode (show detection window) |

## Project Structure

```text
├── main.py              # Entry point
├── config.py            # Global configuration
├── core/                # Core logic
│   ├── bot.py           # Main fishing loop + PD controller
│   ├── detector.py      # Template matching detector
│   ├── yolo_detector.py # YOLO detector
│   ├── screen.py        # Screen capture
│   ├── window.py        # Window management
│   └── input_ctrl.py    # Input control
├── gui/                 # GUI
│   └── app.py
├── utils/               # Utilities
│   └── logger.py
├── img/                 # Template images
├── yolo/                # Legacy YOLO models and training scripts
├── fish_trainer/        # Standalone multi-color fish collection / labeling / migration / training tools
├── 启动.bat            # One-click launch (install + run)
├── install.bat          # Install dependencies only
└── start.bat            # Start program only
```

## YOLO Model Training

If you only want to retrain the legacy YOLO detector currently used by the main app, you can keep using the scripts under `yolo/`:

```bash
python -m yolo.collect
python -m yolo.label
python -m yolo.train
```

The current `yolo.label` workflow also includes a more convenient relabeling flow:

- `python -m yolo.label --predict-model yolo\runs\fish_detect\weights\best.pt`: run auto pre-labeling with an existing model
- `--auto-predict`: run prediction automatically when an image is opened
- Right-click an existing box to switch to that box's class automatically
- Left-click and redraw after selecting a box to overwrite it directly
- `J`: go back to the previous image
- `Ctrl+D`: delete the current image file and jump to the next one
- When flipping pages, boxes are not auto-selected, but the active class is preserved

If you need to relabel images that are already inside `train/` or `val/`:

```bash
python -m yolo.label --relabel
```

If you want a standalone pipeline for collecting, labeling, migrating, and training **multi-color fish**, use the newer `fish_trainer/` toolchain instead.

- Full tool guide: [`fish_trainer/README.en-US.md`](fish_trainer/README.en-US.md)
- Best for: multi-color fish classes, compatibility migration from legacy `fish` labels, and an independent training workflow
- Entry commands:

```bash
python -m fish_trainer.collect
python -m fish_trainer.label
python -m fish_trainer.migrate_labels
python -m fish_trainer.train
```

The main README only keeps the overview. For shortcut keys, class definitions, migration rules, and training parameters, see [`fish_trainer/README.en-US.md`](fish_trainer/README.en-US.md).

## Patch Updates

If you use the EXE build, download the patch zip, extract it next to the EXE, and make sure a `patch/` folder is created. The program will load the patch automatically on startup.

## License

MIT
