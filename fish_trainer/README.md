# Fish Trainer

独立于主程序的多颜色鱼训练工具。

## 目标

- 不改主程序现有 `yolo/` 训练链路
- 先独立采集、打标、训练多颜色鱼模型
- 保留 `fish_generic`，兼容旧的单类 `fish` 标注

## 类别

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

## 用法

采集：

```powershell
python -m fish_trainer.collect --fps 2 --roi
```

标注：

```powershell
python -m fish_trainer.label
python -m fish_trainer.label --relabel
```

迁移旧标注：

```powershell
python -m fish_trainer.migrate_labels
python -m fish_trainer.migrate_labels --with-unlabeled
```

训练：

```powershell
python -m fish_trainer.train --epochs 80
```

## 标注快捷键

- `F`: 通用鱼
- `1-9`: 具体颜色鱼
- `B`: bar
- `T`: track
- `P`: progress
- `N` / `M`: 下一个 / 上一个类别
- `Z`: 撤销
- `X`: 清空当前图片全部标注
- `H`: 再次打印帮助
- `S` / `Enter`: 保存
- `D`: 跳过
- `Q` / `Esc`: 退出

## 旧标注兼容

旧 `yolo/dataset` 的标签类别默认映射为：

- `fish -> fish_generic`
- `bar -> bar`
- `track -> track`
- `progress -> progress`

所以以前的单类 `fish` 标注不会作废，只是会先作为通用鱼类别进入新数据集。
