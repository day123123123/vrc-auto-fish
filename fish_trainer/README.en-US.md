[简体中文](README.zh-CN.md) | [English](README.en-US.md) | [日本語](README.ja-JP.md)

# Fish Trainer

`fish_trainer/` is the standalone multi-color fish training pipeline built on top of the shared `trainer_common/` framework. It keeps its own dataset, labels, runs, and GUI entry, while reusing the same low-level collection / labeling / training logic as `yolo/`.

## Positioning

- `yolo/` is the `runtime_yolo` profile used by the main application
- `fish_trainer/` is the `multicolor` profile used for standalone multi-color fish training
- The two profiles share implementation, but do not share dataset directories or run outputs

Typical use cases for `fish_trainer/`:

- Build a multi-color fish dataset without touching the runtime `yolo/dataset`
- Continue labeling and training through a separate GUI-driven workflow
- Migrate labeled data from the legacy `yolo/dataset`
- Export labeled datasets as zip packages

## Dataset Layout

The default directory structure is:

```text
fish_trainer/
  dataset/
    images/
      unlabeled/
      train/
      val/
    labels/
      train/
      val/
  runs/
```

The standard flow is fixed:

```text
collect -> label -> train
```

- Fresh screenshots first land in `dataset/images/unlabeled/`
- Running `label` moves accepted images into `train/` or `val/` according to `--split`
- Label files are written to `dataset/labels/train/` and `dataset/labels/val/`
- Training output is stored under `runs/`

## Classes And Hotkeys

Current classes and default hotkeys:

- `F`: `fish_generic`
- `1`: `fish_white`
- `2`: `fish_copper`
- `3`: `fish_green`
- `4`: `fish_blue`
- `5`: `fish_purple`
- `6`: `fish_golden`
- `7`: `fish_red`
- `8`: `fish_pink`
- `9`: `fish_rainbow`
- `0`: `fish_teal`
- `B`: `bar`
- `T`: `track`
- `P`: `progress`

Other editor hotkeys:

- `N` / `M`: next / previous class
- `Z`: undo last box
- `X`: clear all labels in the current image
- `H`: print help again
- `S` / `Enter`: save and continue
- `D`: skip current image
- `Q` / `Esc`: quit

## Collection

Common command:

```powershell
python -m fish_trainer.collect --fps 2.0 --roi --max 200
```

Useful options:

- `--fps`: capture rate
- `--roi`: interactively choose a region of interest before capture
- `--max`: stop automatically after capturing N frames

Collected images are written into `dataset/images/unlabeled/`.

## Label New Images

Common command:

```powershell
python -m fish_trainer.label --split 0.2
```

Useful option:

- `--split`: validation ratio used when moving accepted images into `train/` / `val/`

Behavior:

- The tool reads new images from `dataset/images/unlabeled/`
- Saved labels are written to the matching `dataset/labels/...` directory
- Accepted images are moved into `dataset/images/train/` or `dataset/images/val/`

## Relabel Existing Images

To relabel images that are already in `train/` or `val/`:

```powershell
python -m fish_trainer.label --relabel
```

This is useful when you want to correct old boxes or continue labeling an existing dataset.

## Migrate Legacy Data

To import labels from the old `yolo/dataset`:

```powershell
python -m fish_trainer.migrate_labels
python -m fish_trainer.migrate_labels --with-unlabeled
python -m fish_trainer.migrate_labels --source some\old\dataset --overwrite
```

Useful options:

- `--source`: custom source dataset root; defaults to the legacy `yolo/dataset`
- `--overwrite`: overwrite files already present in the target dataset
- `--with-unlabeled`: also copy images that do not currently have label files

Default class mapping:

- `fish -> fish_generic`
- `bar -> bar`
- `track -> track`
- `progress -> progress`

This keeps legacy single-class fish labels usable in the new pipeline.

## Training

Common commands:

```powershell
python -m fish_trainer.train --model yolov8n.pt --epochs 80 --imgsz 640 --batch -1
python -m fish_trainer.train --resume
```

Useful options:

- `--model`: base model or checkpoint path
- `--epochs`: training epochs
- `--imgsz`: image size
- `--batch`: batch size, `-1` lets Ultralytics pick automatically
- `--resume`: resume the latest interrupted run

## GUI Entry

You can also launch the GUI entry directly:

```powershell
python -m fish_trainer.gui
```

The GUI provides buttons for collection, labeling, relabeling, migration, training, and exporting datasets.

## Note On `fish_teal`

- The labeler already supports `fish_teal` on key `0`
- Whether the training YAML currently declares `fish_teal` depends on the synced dataset config
- Treat the labeler behavior and the training YAML declaration as related, but not automatically identical
