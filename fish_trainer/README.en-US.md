[简体中文](README.zh-CN.md) | [English](README.en-US.md) | [日本語](README.ja-JP.md)

# Fish Trainer

A standalone training toolchain for multi-color fish, separate from the main application.

## Goals

- Keep the existing `yolo/` training pipeline in the main app unchanged
- Collect data, label images, and train a multi-color fish model independently
- Preserve `fish_generic` for compatibility with the legacy single-class `fish` label

## Classes

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

## Usage

Collection:

```powershell
python -m fish_trainer.collect --fps 2 --roi
```

Labeling:

```powershell
python -m fish_trainer.label
python -m fish_trainer.label --relabel
```

Migrate legacy labels:

```powershell
python -m fish_trainer.migrate_labels
python -m fish_trainer.migrate_labels --with-unlabeled
```

Training:

```powershell
python -m fish_trainer.train --epochs 80
```

## Labeling Hotkeys

- `F`: generic fish
- `1-9`: specific fish colors
- `B`: `bar`
- `T`: `track`
- `P`: `progress`
- `N` / `M`: next / previous class
- `Z`: undo
- `X`: clear all labels in the current image
- `H`: print help again
- `S` / `Enter`: save
- `D`: skip
- `Q` / `Esc`: quit

## Legacy Label Compatibility

Labels from the old `yolo/dataset` are mapped by default as follows:

- `fish -> fish_generic`
- `bar -> bar`
- `track -> track`
- `progress -> progress`

This means your old single-class `fish` labels are still usable. They simply enter the new dataset as the generic fish class first.
