# BOOTH 配布用ランチャー

`booth_launcher.py` は、日本語ユーザー向けの自動更新ランチャーです。

このランチャーを起動すると、次の処理を自動実行します。

1. GitHub からソースコードを初回 clone
2. 既存 repo の場合は更新有無を確認
3. 更新が見つかった場合は、更新内容を表示して「今すぐ更新するか」を確認
4. 更新に同意したときだけ repo を update
5. 仮想環境 `.venv` を作成
6. `pip` / `setuptools` / `wheel` を更新
7. GPU を自動判定して `torch` / `torchvision` をインストール
8. `requirements.txt` をインストール
9. `main.py` を起動

## 事前条件

- Windows
- Git for Windows がインストール済み
- Python 3.10 以上がインストール済み

未インストールの場合、ランチャーは日本語メッセージで案内します。

## 設定

同梱の `booth_launcher_config.json` を編集すると、次の項目を変更できます。

- `repo_url`: 配布先ユーザーが clone する Git リポジトリ
- `branch`: 追従するブランチ
- `app_dir_name`: `LOCALAPPDATA` 配下に作る作業ディレクトリ名
- `repo_dir_name`: clone 先フォルダ名
- `venv_dir_name`: 仮想環境名
- `entrypoint`: 起動する Python ファイル
- `show_update_prompt`: 更新確認ダイアログを出すかどうか
- `update_log_count`: ダイアログに表示する更新件数

## EXE 化

PyInstaller を使う場合:

```bat
build_booth_launcher.bat
```

または:

```bat
python -m PyInstaller --noconfirm booth_launcher.spec
```

ビルド後の出力先:

```text
dist\VRC Auto Fish Launcher\
```

## BOOTH 配布時のおすすめ同梱物

- `VRC Auto Fish Launcher.exe`
- `booth_launcher_config.json`
- `README.booth.ja-JP.md`

## 注意

- 本体ソース側が起動時に `settings.json` などを書き換えるため、ランチャーは update 前にローカル変更を自動で `git stash` してから pull します。
- ユーザーに Git と Python を入れさせたくない場合は、このランチャーとは別に「Python 同梱型の本体配布」へ設計を変える必要があります。
