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

ERR_LOG="setup_error.log"
: > "$ERR_LOG"

# --- 1. Python -----------------------------------------------------------
step "Python を確認しています"

# 使える Python を探す。
# 見るべき点が2つある:
#   1) PATH の通っていない場所に入っていることがある
#      （macOS 標準の /usr/bin/python3 は 3.9 で古く、更新できない）
#   2) インストールが壊れていることがある。特に Homebrew の Python で
#      pyexpat が system の libexpat とシンボル不一致になると
#      import plistlib が失敗し、platform.mac_ver() が空を返す。
#      こうなると pip は wheel 判定も証明書処理もできず全滅するので、
#      候補の時点で除外する。
python_is_healthy() {
    "$1" - <<'PYEOF' 2>/dev/null
import sys

ok = sys.version_info[:2] >= (3, 10)
if ok and sys.platform == "darwin":
    try:
        import plistlib  # noqa: F401  (pyexpat が壊れているとここで落ちる)
        import platform
        ok = bool(platform.mac_ver()[0])
    except Exception:
        ok = False
sys.exit(0 if ok else 1)
PYEOF
}

list_pythons() {
    local candidate resolved seen=""
    for version in 3.13 3.12 3.14 3.11 3.10; do
        for candidate in \
            "python$version" \
            "/Library/Frameworks/Python.framework/Versions/$version/bin/python3" \
            "/opt/homebrew/bin/python$version" \
            "/usr/local/bin/python$version"
        do
            command -v "$candidate" >/dev/null 2>&1 || continue
            python_is_healthy "$candidate" || continue
            resolved=$("$candidate" -c 'import sys; print(sys.executable)' 2>/dev/null) || continue
            case " $seen " in *" $resolved "*) continue ;; esac
            seen="$seen $resolved"
            printf '%s\n' "$candidate"
        done
    done
    for candidate in python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        python_is_healthy "$candidate" || continue
        resolved=$("$candidate" -c 'import sys; print(sys.executable)' 2>/dev/null) || continue
        case " $seen " in *" $resolved "*) continue ;; esac
        seen="$seen $resolved"
        printf '%s\n' "$candidate"
    done
}

# 使える Python が1つも無い場合の逃げ道。
# uv は自前の Python（libexpat も同梱）を持ってくるので、
# Homebrew や system のライブラリが壊れていても影響を受けない。
# wheel の判定も Rust 側で行うため platform.mac_ver() に依存しない。
setup_with_uv() {
    local uv_bin=""
    if command -v uv >/dev/null 2>&1; then
        uv_bin=$(command -v uv)
    elif [ -x "$HOME/.local/bin/uv" ]; then
        uv_bin="$HOME/.local/bin/uv"
    else
        warn "uv を導入します（1回だけ。~/.local/bin に入ります）"
        curl -LsSf https://astral.sh/uv/install.sh 2>>"$ERR_LOG" | sh >>"$ERR_LOG" 2>&1 || return 1
        uv_bin="$HOME/.local/bin/uv"
    fi
    [ -x "$uv_bin" ] || command -v "$uv_bin" >/dev/null 2>&1 || return 1
    ok "uv: $("$uv_bin" --version 2>&1)"

    warn "独立した Python 3.12 を取得しています（数分かかることがあります）"
    printf '\n=== uv: python install ===\n' >>"$ERR_LOG"
    "$uv_bin" python install 3.12 >>"$ERR_LOG" 2>&1 || return 1
    rm -rf .venv
    # --python-preference only-managed: システムや Homebrew の壊れた Python を
    # 掴まないよう、uv が持ってきた自己完結の Python だけを使う
    printf '\n=== uv: venv ===\n' >>"$ERR_LOG"
    "$uv_bin" venv --python 3.12 --python-preference only-managed .venv >>"$ERR_LOG" 2>&1 || return 1
    printf '\n=== uv: pip install ===\n' >>"$ERR_LOG"
    "$uv_bin" pip install --python .venv/bin/python -r requirements.txt >>"$ERR_LOG" 2>&1 || return 1
    return 0
}

# 除外された（壊れている）Python を報告するため、別途集める
list_broken_pythons() {
    local candidate seen=""
    for candidate in python3 python3.14 python3.13 python3.12 python3.11 python3.10; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        "$candidate" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null || continue
        python_is_healthy "$candidate" && continue
        printf '%s (%s)\n' "$candidate" "$("$candidate" -V 2>&1)"
    done
}

PYTHONS=$(list_pythons || true)
USED_UV=0
if [ -z "$PYTHONS" ]; then
    BROKEN=$(list_broken_pythons || true)
    if [ -n "$BROKEN" ]; then
        warn "次の Python は壊れているため使えません（pyexpat が読み込めず pip が動きません）:"
        printf '%s\n' "$BROKEN" | sed 's/^/        /'
    fi

    step "uv で独立した Python を用意します"
    if setup_with_uv; then
        USED_UV=1
        VENV_PY=".venv/bin/python"
        ok "uv で .venv を作成し、ライブラリも導入しました"
    elif [ -n "$BROKEN" ]; then
        die "使える Python が見つかりません。

  Homebrew の Python でよくある不具合です（pyexpat と libexpat の不一致）。
  次のどれかで直してください:

    A) Homebrew の Python を入れ直す（この Mac 全体が直るのでおすすめ）
         brew update && brew reinstall python@3.13

    B) それでも直らなければ uv を使う（独立した Python を持ってくる）
         curl -LsSf https://astral.sh/uv/install.sh | sh
         bash setup_mac.sh          ← 再実行すれば uv を自動で使います

    C) python.org のインストーラで入れ直す
         https://www.python.org/downloads/macos/

  直ったかどうかは次で確認できます（バージョンが出れば OK）:
       python3.13 -c 'import platform; print(platform.mac_ver())'"
    fi

    CURRENT="（python3 が見つかりません）"
    if command -v python3 >/dev/null 2>&1; then
        CURRENT="現在の python3 は $(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])') です（$(command -v python3)）"
    fi
    die "使える Python (3.10 以上) が見つかりません。
  $CURRENT

  macOS に最初から入っている Python は 3.9 で、更新できません。
  次のどちらかで新しい Python を入れてください:

    A) Homebrew をお使いなら
         brew install python@3.12

    B) Homebrew がない場合
         https://www.python.org/downloads/macos/ から
         「macOS 64-bit universal2 installer」をダウンロードして実行

  インストール後、このスクリプトをもう一度実行してください:
       bash setup_mac.sh"
fi

PY_COUNT=$(printf '%s\n' "$PYTHONS" | grep -c . || true)
PY_FIRST=$(printf '%s\n' "$PYTHONS" | head -1)
ok "$("$PY_FIRST" -c 'import sys; print("Python %d.%d.%d" % sys.version_info[:3])') ($PY_FIRST)"
[ "$PY_COUNT" -gt 1 ] && ok "ほかに $((PY_COUNT - 1)) 個の候補あり（失敗したら自動で切り替えます）"

# --- 2. 仮想環境 ---------------------------------------------------------
step "仮想環境 (.venv) を用意しています"


# venv を作る。
# 健康な Python（plistlib が読めて mac_ver が取れるもの）しか候補に入れていないので、
# 素直に作れるのが正常。念のため ensurepip を分けて試す段を1つだけ残す。
#
# 以前あった truststore 回避 (PIP_USE_DEPRECATED) は削除した。
# ensurepip は起動時に PIP_* を全て削除する（CPython の
# ensurepip._disable_pip_configuration_settings）ため効果がないうえ、
# pip 24.0 では legacy-certs が不正な選択肢となり別の失敗を招くため。
try_make_venv() {
    local py="$1"

    printf '\n=== [1] %s: python -m venv ===\n' "$py" >>"$ERR_LOG"
    rm -rf .venv
    if "$py" -m venv .venv >>"$ERR_LOG" 2>&1 && venv_pip_works; then
        return 0
    fi

    printf '\n=== [2] %s: --without-pip + ensurepip ===\n' "$py" >>"$ERR_LOG"
    rm -rf .venv
    "$py" -m venv --without-pip .venv >>"$ERR_LOG" 2>&1 || return 1
    .venv/bin/python -m ensurepip --upgrade --default-pip >>"$ERR_LOG" 2>&1 || return 1
    venv_pip_works || return 1
    return 0
}

# pip の実行ファイルがあるだけでは不十分。実際に動くところまで確認する。
venv_pip_works() {
    [ -x .venv/bin/python ] || return 1
    .venv/bin/python -m pip --version >>"$ERR_LOG" 2>&1
}

if [ "$USED_UV" = "1" ]; then
    :   # uv で作成済み
elif venv_pip_works; then
    ok "既存の .venv を再利用します"
    VENV_PY=".venv/bin/python"
else
    : > "$ERR_LOG"
    VENV_PY=""
    while IFS= read -r py; do
        [ -n "$py" ] || continue
        if try_make_venv "$py"; then
            VENV_PY=".venv/bin/python"
            ok ".venv を作成しました（$("$VENV_PY" -c 'import sys; print("Python %d.%d.%d" % sys.version_info[:3])')）"
            break
        fi
        warn "$py では作成できませんでした。次の Python を試します"
    done <<< "$PYTHONS"

    if [ -z "$VENV_PY" ]; then
        printf "\n--- 詳細ログ（段階ごと）-------------------------------\n" >&2
        tail -60 "$ERR_LOG" >&2
        printf -- "-------------------------------------------------------\n" >&2
        die "仮想環境を作成できませんでした。
  上の詳細ログ（$ERR_LOG に全文あり）をそのまま共有してください。

  よくある原因:
    ・Python のインストールが不完全 → 入れ直す、または brew install python@3.12
    ・社内ネットワークで bootstrap.pypa.io に到達できない"
    fi
    rm -f "$ERR_LOG"
fi

# --- 3. 依存関係 ---------------------------------------------------------
step "必要なライブラリをインストールしています（数分かかります）"
if [ "$USED_UV" = "1" ]; then
    ok "uv で導入済みです"
elif ! "$VENV_PY" -m pip install --quiet -r requirements.txt; then
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
