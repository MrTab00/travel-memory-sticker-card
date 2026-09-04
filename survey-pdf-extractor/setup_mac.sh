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

# PATH の通っていない場所に入っていることがあるので、候補を順に探す
# （macOS 標準の /usr/bin/python3 は 3.9 で古く、更新できない）
find_python() {
    local candidate
    for version in 3.13 3.12 3.11 3.10; do
        for candidate in \
            "python$version" \
            "/Library/Frameworks/Python.framework/Versions/$version/bin/python3" \
            "/opt/homebrew/bin/python$version" \
            "/usr/local/bin/python$version"
        do
            if command -v "$candidate" >/dev/null 2>&1 &&
               "$candidate" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null; then
                printf '%s' "$candidate"
                return 0
            fi
        done
    done
    if command -v python3 >/dev/null 2>&1 &&
       python3 -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null; then
        printf 'python3'
        return 0
    fi
    return 1
}

if ! PYBIN=$(find_python); then
    CURRENT="（python3 が見つかりません）"
    if command -v python3 >/dev/null 2>&1; then
        CURRENT="現在の python3 は $(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])') です（$(command -v python3)）"
    fi
    die "使える Python (3.10 以上) が見つかりません。
  $CURRENT

  macOS に最初から入っている Python は 3.9 で、更新できません。
  次のどちらかで新しい Python を入れてください:

    A) Homebrew をお使いなら（ターミナルで完結・おすすめ）
         brew install python@3.12

    B) Homebrew がない場合
         https://www.python.org/downloads/macos/ を開き、
         最新版（3.13.x）の「macOS 64-bit universal2 installer」を
         ダウンロードして実行してください。

  インストール後、このスクリプトをもう一度実行してください:
       bash setup_mac.sh"
fi

PY_VERSION=$("$PYBIN" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')
ok "Python $PY_VERSION ($(command -v "$PYBIN"))"

# --- 2. 仮想環境 ---------------------------------------------------------
step "仮想環境 (.venv) を用意しています"
if [ -x .venv/bin/python ]; then
    ok "既存の .venv を再利用します"
else
    "$PYBIN" -m venv .venv || die "仮想環境を作成できませんでした。ディスクの空き容量と書き込み権限をご確認ください。"
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

# --- 5. APIキー用ファイル ------------------------------------------------
step "APIキーの設定ファイルを用意しています"
if [ -f .env ]; then
    ok ".env は既にあります（中身はそのまま）"
else
    cp .env.example .env
    ok ".env を作成しました（キーはまだ空です）"
fi
chmod 600 .env
ok "権限を 600（自分だけが読める）に設定しました"
warn "Git にはコミットされません（.gitignore 済み）"

# --- 6. 動作確認（API は呼ばないので課金なし）----------------------------
step "動作確認（ダミーデータ／API は呼びません）"
if "$VENV_PY" main.py --self-test >/dev/null 2>&1; then
    ok "Excel の生成まで正常に動きました"
else
    warn "セルフテストに失敗しました。次のコマンドの出力を共有してください:"
    printf "      .venv/bin/python main.py --self-test\n"
fi

# --- 7. 次にやること -----------------------------------------------------
step "セットアップ完了"
cat <<'EOS'

次の手順で実行してください（このフォルダで）。

  1) APIキーを .env に書く（ターミナルには入力しない＝履歴に残らない）

       open -e .env          ← テキストエディットで開く

     開いたら一番下の行を、キーを貼り付けて次の形にして保存:

       ANTHROPIC_API_KEY=sk-ant-...

     ※ 必要なときだけ書き、使い終わったら消しておく運用でも動きます。
     ※ このファイルは自分だけが読める権限で、Git にも入りません。

  2) スキャンした PDF を input/ に入れる（Finder でドラッグ＆ドロップで可）

       open .

  3) 実行する
       ./run.sh --show-groups     ← 右上の手書き番号でのグループ分けを確認（課金なし）
       ./run.sh --dry-run         ← 概算コストを確認（課金なし）
       ./run.sh --only No01       ← まず1名だけ。原本と突き合わせる
       ./run.sh                   ← 全員分 → output/aggregate_YYYYMMDD.xlsx

       open output                ← 結果のフォルダを開く

  困ったときは、ターミナルに出たメッセージをそのまま共有してください。
  （キーは伏せ字で表示されるので、そのまま貼っても漏れません）

EOS
