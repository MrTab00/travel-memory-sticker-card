#!/usr/bin/env bash
# =============================================================================
# アンケートPDF 自動抽出・集計ツール — macOS セットアップ
#
#   bash setup_mac.sh
#
# 仮想環境の作成・依存関係のインストール・動作確認までを一度に行う。
# 何度実行しても壊れない（途中で失敗したら、直してもう一度実行すればよい）。
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")"
GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; RED=$'\033[0;31m'; BOLD=$'\033[1m'; NC=$'\033[0m'

step() { printf "\n%s==> %s%s\n" "$BOLD" "$1" "$NC"; }
ok()   { printf "    %s✓%s %s\n" "$GREEN" "$NC" "$1"; }
warn() { printf "    %s!%s %s\n" "$YELLOW" "$NC" "$1"; }
die()  { printf "\n%sエラー:%s %s\n" "$RED" "$NC" "$1" >&2; exit 1; }

# --- 1. Python -----------------------------------------------------------
step "Python を確認しています"

if ! command -v python3 >/dev/null 2>&1; then
    die "python3 が見つかりません。

  次のいずれかで Python 3.11 以上を入れてください:
    A) https://www.python.org/downloads/macos/ からインストーラをダウンロード（おすすめ）
    B) Homebrew が入っていれば:  brew install python@3.12

  インストール後、ターミナルを開き直してもう一度 bash setup_mac.sh を実行してください。"
fi

PY_VERSION=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info[:2] >= (3, 10) else 0)')
if [ "$PY_OK" != "1" ]; then
    die "Python $PY_VERSION では動きません（3.10 以上が必要、3.11 以上を推奨）。

  macOS に最初から入っている python3 は古いことがあります。
  https://www.python.org/downloads/macos/ から新しい Python を入れ、
  ターミナルを開き直してもう一度実行してください。"
fi
ok "Python $PY_VERSION ($(command -v python3))"

# --- 2. 仮想環境 ---------------------------------------------------------
step "仮想環境 (.venv) を用意しています"
if [ -x .venv/bin/python ]; then
    ok "既存の .venv を再利用します"
else
    python3 -m venv .venv || die "仮想環境を作成できませんでした。ディスクの空き容量と書き込み権限をご確認ください。"
    ok ".venv を作成しました"
fi
VENV_PY=".venv/bin/python"

# --- 3. 依存関係 ---------------------------------------------------------
step "必要なライブラリをインストールしています（数分かかります）"
"$VENV_PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || warn "pip の更新に失敗しましたが続行します"
if ! "$VENV_PY" -m pip install --quiet -r requirements.txt; then
    die "ライブラリのインストールに失敗しました。

  社内ネットワークのプロキシや SSL 検査が原因のことがあります。
  その場合は次を試してください:
    .venv/bin/python -m pip install -r requirements.txt --proxy http://<プロキシ:ポート>"
fi
"$VENV_PY" -c "import anthropic, pypdf, pandas, openpyxl, yaml, pymupdf" \
    || die "ライブラリの読み込みに失敗しました。上のエラーメッセージをそのまま共有してください。"
ok "anthropic / pypdf / pandas / openpyxl / PyYAML / PyMuPDF"

# --- 4. 作業フォルダ -----------------------------------------------------
step "入出力フォルダを用意しています"
mkdir -p input output
ok "input/  … ここにスキャンした PDF を置きます"
ok "output/ … 結果の Excel と中間データがここに出ます"

# --- 5. 動作確認（API は呼ばないので課金なし）----------------------------
step "動作確認（ダミーデータ／API は呼びません）"
if "$VENV_PY" main.py --self-test >/dev/null 2>&1; then
    ok "Excel の生成まで正常に動きました"
else
    warn "セルフテストに失敗しました。次のコマンドの出力を共有してください:"
    printf "      .venv/bin/python main.py --self-test\n"
fi

# --- 6. 次にやること -----------------------------------------------------
step "セットアップ完了"
cat <<'EOS'

次の手順で実行してください（このフォルダで）。

  1) API キーを設定する（このターミナルの間だけ有効）
       export ANTHROPIC_API_KEY='sk-ant-...'

     ※ 毎回設定するのが面倒なら、次を一度だけ実行してターミナルを開き直す
       echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.zshrc

  2) スキャンした PDF を input/ に入れる（Finder でドラッグ＆ドロップで可）

  3) 実行する
       ./run.sh --show-groups     ← 右上の手書き番号でのグループ分けを確認（課金なし）
       ./run.sh --dry-run         ← 概算コストを確認（課金なし）
       ./run.sh --only No01       ← まず1名だけ。原本と突き合わせる
       ./run.sh                   ← 全員分 → output/aggregate_YYYYMMDD.xlsx

  困ったときは、ターミナルに出たメッセージをそのまま共有してください。

EOS
