[简体中文](README.zh-CN.md) | [English](README.en-US.md) | [日本語](README.ja-JP.md)

# VRChat 自動釣りアシスタント (FISH!)

VRChat ワールド **FISH!** 向けの自動釣りスクリプトです。YOLO 物体検出と PD コントローラに対応し、キャスト、フッキング、ミニゲーム操作を自動化できます。

## 機能

- **自動キャスト / 自動フッキング**: 食いつきアニメーションを検出して釣りの一連の流れを自動実行
- **ミニゲーム自動操作**: PD コントローラで魚の位置を追跡し、白いバーを自動制御
- **YOLO 物体検出**: 学習後はテンプレートマッチングの代わりに利用でき、精度を向上
- **GUI**: パラメータを視覚的に調整でき、リアルタイムのデバッグウィンドウも利用可能
- **ホットキー操作**: `F9` で開始/一時停止、`F10` で停止、`F11` でデバッグモード
- **VRChat OSC 入力**: マウスを占有しない任意の OSC 入力方式に対応

## クイックスタート

### 方法1: ワンクリック起動（推奨）

1. [Python 3.10+](https://www.python.org/downloads/) をインストールし、**Add to PATH** を有効にする
2. `启动.bat` をダブルクリックする。初回のみ依存関係を自動インストールし、その後は直接起動される

> GPU は自動判定されます。NVIDIA では CUDA 版、AMD / Intel では CPU 版がインストールされます。

### 方法2: 手動インストール

```bash
# PyTorch をインストール（GPU 版）
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

# または CPU 版
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# そのほかの依存関係をインストール
pip install -r requirements.txt

# 起動
python main.py
```

## 使い方

1. VRChat を起動して `FISH!` ワールドに入る
2. プログラムを起動し、「选择窗口」をクリックして VRChat ウィンドウを関連付ける
3. 必要に応じて「框选区域」をクリックし、釣りミニゲームの検出範囲を指定する
4. `F9` を押して自動釣りを開始する

## ホットキー

| キー | 機能 |
| --- | --- |
| `F9` | 開始 / 一時停止 |
| `F10` | 停止 |
| `F11` | デバッグモード（検出ウィンドウを表示） |

## プロジェクト構成

```text
├── main.py              # エントリーポイント
├── config.py            # グローバル設定
├── core/                # コアロジック
│   ├── bot.py           # 釣りメインループ + PD コントローラ
│   ├── detector.py      # テンプレートマッチング検出
│   ├── yolo_detector.py # YOLO 検出
│   ├── screen.py        # 画面キャプチャ
│   ├── window.py        # ウィンドウ管理
│   └── input_ctrl.py    # 入力制御
├── gui/                 # GUI
│   └── app.py
├── utils/               # ユーティリティ
│   └── logger.py
├── img/                 # テンプレート画像
├── yolo/                # 旧 YOLO モデルと学習スクリプト
├── fish_trainer/        # 独立した多色魚の収集 / ラベル付け / 移行 / 学習ツール
├── 启动.bat            # ワンクリック起動（インストール + 実行）
├── install.bat          # 依存関係のみインストール
└── start.bat            # プログラムのみ起動
```

## YOLO モデル学習

メインアプリで現在使っている旧 YOLO 検出モデルだけを再学習したい場合は、`yolo/` 配下の旧スクリプトをそのまま使えます。

```bash
python -m yolo.collect
python -m yolo.label
python -m yolo.train
```

現在の `yolo.label` では、より扱いやすい再ラベル付けフローも利用できます。

- `python -m yolo.label --predict-model yolo\runs\fish_detect\weights\best.pt`: 既存モデルで自動プレラベルを実行
- `--auto-predict`: 画像を開いたときに自動で予測を実行
- 既存ボックスを右クリックすると、そのボックスのクラスに自動で切り替え
- ボックス選択後に左クリックで描き直すと、既存ボックスをそのまま上書き
- `J`: 前の画像に戻る
- `Ctrl+D`: 現在の画像ファイルを削除して次の画像へ進む
- ページ切り替え時にボックスは自動選択されないが、現在のアクティブクラスは維持される

すでに `train/` または `val/` に入っている画像を再ラベル付けしたい場合:

```bash
python -m yolo.label --relabel
```

多色魚の収集、ラベル付け、旧ラベル移行、学習を独立した流れで行いたい場合は、新しい `fish_trainer/` ツールチェーンを使ってください。

- ツール全体の説明: [`fish_trainer/README.ja-JP.md`](fish_trainer/README.ja-JP.md)
- 向いている用途: 多色魚クラス、旧 `fish` ラベルとの互換移行、独立した学習ワークフロー
- 起動コマンド:

```bash
python -m fish_trainer.collect
python -m fish_trainer.label
python -m fish_trainer.migrate_labels
python -m fish_trainer.train
```

この README では概要のみを扱います。ショートカットキー、クラス定義、移行ルール、学習パラメータの詳細は [`fish_trainer/README.ja-JP.md`](fish_trainer/README.ja-JP.md) を参照してください。

## パッチ更新

EXE 版を使う場合は、パッチ zip をダウンロードして EXE と同じ階層に展開し、`patch/` フォルダが生成されていることを確認してください。起動時に自動で読み込まれます。

## License

MIT
