[简体中文](README.zh-CN.md) | [English](README.en-US.md) | [日本語](README.ja-JP.md)

# Fish Trainer

`fish_trainer/` は、共有フレームワーク `trainer_common/` の上に構築された独立多色魚学習パイプラインです。`yolo/` と収集 / ラベル付け / 学習の低レベル実装を共有しつつ、専用のデータセット、ラベル、学習出力、GUI 入口を持ちます。

## 位置づけ

- `yolo/` はメインアプリが使う `runtime_yolo` profile
- `fish_trainer/` は独立多色魚学習用の `multicolor` profile
- 2 つの profile は実装を共有しますが、データセットと学習出力ディレクトリは共有しません

`fish_trainer/` が向いている用途:

- 実行時用 `yolo/dataset` に触れずに多色魚データセットを構築したい
- GUI 中心の独立ワークフローで継続してラベル付けや学習を進めたい
- 旧 `yolo/dataset` からラベル済みデータを移行したい
- ラベル済みデータセットを zip 形式で出力したい

## データディレクトリ構成

既定のディレクトリ構成:

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

標準フローは次の通りです:

```text
collect -> label -> train
```

- 新規スクリーンショットはまず `dataset/images/unlabeled/` に入ります
- `label` を実行すると、採用された画像は `--split` に従って `train/` または `val/` に移動します
- ラベルファイルは `dataset/labels/train/` と `dataset/labels/val/` に保存されます
- 学習出力は `runs/` 配下に生成されます

## クラスとショートカット

現在のクラスと既定ショートカット:

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

その他の編集ショートカット:

- `N` / `M`: 次 / 前のクラス
- `Z`: 直前のボックスを取り消し
- `X`: 現在画像のラベルをすべて削除
- `H`: ヘルプを再表示
- `S` / `Enter`: 保存して続行
- `D`: 現在画像をスキップ
- `Q` / `Esc`: 終了

## 収集

よく使うコマンド:

```powershell
python -m fish_trainer.collect --fps 2.0 --roi --max 200
```

主なオプション:

- `--fps`: 収集フレームレート
- `--roi`: 収集前に対象領域を対話的に選択
- `--max`: N 枚収集したら自動停止

収集された画像は `dataset/images/unlabeled/` に保存されます。

## 新規画像のラベル付け

よく使うコマンド:

```powershell
python -m fish_trainer.label --split 0.2
```

主なオプション:

- `--split`: 採用画像を `train/` / `val/` に振り分ける検証比率

挙動:

- 新規画像は `dataset/images/unlabeled/` から読み込みます
- 保存したラベルは対応する `dataset/labels/...` に書き込まれます
- 採用画像は `dataset/images/train/` または `dataset/images/val/` に移動します

## 既存画像の再ラベル付け

すでに `train/` または `val/` にある画像を再ラベル付けする場合:

```powershell
python -m fish_trainer.label --relabel
```

古いボックスを修正したい場合や、既存データセットへの追加入力を続けたい場合に便利です。

## 旧データの移行

旧 `yolo/dataset` からラベルを取り込む場合:

```powershell
python -m fish_trainer.migrate_labels
python -m fish_trainer.migrate_labels --with-unlabeled
python -m fish_trainer.migrate_labels --source some\old\dataset --overwrite
```

主なオプション:

- `--source`: 取り込み元データセットルート。既定は旧 `yolo/dataset`
- `--overwrite`: すでに存在する対象ファイルを上書き
- `--with-unlabeled`: ラベルファイルがない画像も一緒にコピー

既定クラスマッピング:

- `fish -> fish_generic`
- `bar -> bar`
- `track -> track`
- `progress -> progress`

これにより、旧単一クラス魚ラベルも新しいパイプラインで引き続き利用できます。

## 学習

よく使うコマンド:

```powershell
python -m fish_trainer.train --model yolov8n.pt --epochs 80 --imgsz 640 --batch -1
python -m fish_trainer.train --resume
```

主なオプション:

- `--model`: ベースモデルまたはチェックポイントパス
- `--epochs`: 学習エポック数
- `--imgsz`: 画像サイズ
- `--batch`: バッチサイズ。`-1` は Ultralytics に自動選択させます
- `--resume`: 直近の中断学習を再開

## GUI 入口

GUI から起動することもできます:

```powershell
python -m fish_trainer.gui
```

GUI では収集、ラベル付け、再ラベル付け、移行、学習、データセット出力をボタンから実行できます。

## `fish_teal` について

- ラベラーはすでにキー `0` の `fish_teal` をサポートしています
- 学習 YAML が `fish_teal` を宣言しているかは、同期されているデータセット設定に依存します
- ラベラーの挙動と学習 YAML の宣言は関連していますが、自動的に完全一致するとは限りません
