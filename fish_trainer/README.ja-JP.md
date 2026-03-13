[简体中文](README.zh-CN.md) | [English](README.en-US.md) | [日本語](README.ja-JP.md)

# Fish Trainer

メインアプリとは独立した、多色魚向けの学習ツールチェーンです。

## 目的

- メインアプリの既存 `yolo/` 学習パイプラインは変更しない
- 多色魚モデルの収集、ラベル付け、学習を独立して進める
- 旧来の単一クラス `fish` ラベルとの互換性のために `fish_generic` を維持する

## クラス

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

## 使い方

収集:

```powershell
python -m fish_trainer.collect --fps 2 --roi
```

ラベル付け:

```powershell
python -m fish_trainer.label
python -m fish_trainer.label --relabel
```

旧ラベルの移行:

```powershell
python -m fish_trainer.migrate_labels
python -m fish_trainer.migrate_labels --with-unlabeled
```

学習:

```powershell
python -m fish_trainer.train --epochs 80
```

## ラベル付けショートカット

- `F`: 汎用魚
- `1-9`: 個別の魚色クラス
- `B`: `bar`
- `T`: `track`
- `P`: `progress`
- `N` / `M`: 次 / 前のクラス
- `Z`: 元に戻す
- `X`: 現在の画像のラベルをすべて削除
- `H`: ヘルプを再表示
- `S` / `Enter`: 保存
- `D`: スキップ
- `Q` / `Esc`: 終了

## 旧ラベル互換

旧 `yolo/dataset` のラベルは、デフォルトで次のようにマッピングされます。

- `fish -> fish_generic`
- `bar -> bar`
- `track -> track`
- `progress -> progress`

そのため、以前の単一クラス `fish` ラベルは無効になりません。まずは汎用魚クラスとして新しいデータセットに取り込まれます。
