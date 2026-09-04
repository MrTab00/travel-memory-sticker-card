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
list_pythons() {
    local candidate resolved seen=""
    for version in 3.13 3.12 3.11 3.10 ""; do
        for candidate in \
            "python$version" \
            "/Library/Frameworks/Python.framework/Versions/$version/bin/python3" \
            "/opt/homebrew/bin/python$version" \
            "/usr/local/bin/python$version"
        do
            command -v "$candidate" >/dev/null 2>&1 || continue
            "$candidate" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null || continue
            resolved=$("$candidate" -c 'import sys; print(sys.executable)' 2>/dev/null) || continue
            case " $seen " in *" $resolved "*) continue ;; esac
            seen="$seen $resolved"
            printf '%s\n' "$candidate"
        done
    done
}

PYTHONS=$(list_pythons || true)
if [ -z "$PYTHONS" ]; then
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

PY_COUNT=$(printf '%s\n' "$PYTHONS" | grep -c . || true)
PY_FIRST=$(printf '%s\n' "$PYTHONS" | head -1)
ok "$("$PY_FIRST" -c 'import sys; print("Python %d.%d.%d" % sys.version_info[:3])') ($PY_FIRST)"
[ "$PY_COUNT" -gt 1 ] && ok "ほかに $((PY_COUNT - 1)) 個の候補あり（失敗したら自動で切り替えます）"

# --- 2. 仮想環境 ---------------------------------------------------------
step "仮想環境 (.venv) を用意しています"

ERR_LOG="setup_error.log"

# truststore を含まない最後の pip。新しい pip が動かない環境の逃げ道に使う
PIP_WHEEL_NAME="pip-24.0-py3-none-any.whl"
PIP_WHEEL_URL="https://files.pythonhosted.org/packages/py3/p/pip/$PIP_WHEEL_NAME"
PIP_FIX_APPLIED=0
MACVER_FIX_APPLIED=0

# この Mac では platform.mac_ver() が空文字を返すことがある。
# pip は wheel の対応判定 (packaging.tags.mac_platforms) と証明書処理 (truststore) の
# 両方で macOS のバージョンを int() に渡すため、そこで ValueError になり
# venv 作成・ensurepip・get-pip.py がまとめて失敗する。
# → venv の中に「起動時に sw_vers から本当のバージョンを補う」パッチを仕込む。
#    sitecustomize.py は同名のシステム側ファイルに隠されることがあるため .pth を使う。
install_macver_fix() {
    local site
    site=$(.venv/bin/python -c 'import site; print(site.getsitepackages()[0])' 2>/dev/null) || return 1
    [ -n "$site" ] || return 1
    mkdir -p "$site"

    cat > "$site/_macver_fix.py" <<'PYEOF'
"""platform.mac_ver() が空を返す macOS 向けの補正。

pip は macOS のバージョンを int() に渡すため、空文字だと ValueError で落ちる。
sw_vers（無理なら SystemVersion.plist）から実際の値を補って返す。
この環境以外では何もしない。
"""
import sys

if sys.platform == "darwin":
    try:
        import platform

        if not platform.mac_ver()[0]:
            _version = ""
            try:
                import subprocess

                _version = subprocess.run(
                    ["/usr/bin/sw_vers", "-productVersion"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                ).stdout.strip()
            except Exception:
                pass

            if not _version:
                try:
                    import plistlib

                    with open(
                        "/System/Library/CoreServices/SystemVersion.plist", "rb"
                    ) as _f:
                        _version = plistlib.load(_f).get("ProductVersion", "")
                except Exception:
                    pass

            if not _version:
                _version = "13.0"  # 最終手段（多くの wheel が対応する保守的な値）

            _machine = platform.machine() or "x86_64"
            platform.mac_ver = lambda *a, **k: (_version, ("", "", ""), _machine)
    except Exception:
        pass
PYEOF

    printf 'import _macver_fix\n' > "$site/zz_macver_fix.pth"
    return 0
}

# venv を作る。失敗したら手を替えて再試行する。
try_make_venv() {
    local py="$1"

    # (1) 素直に作る
    rm -rf .venv
    if "$py" -m venv .venv >>"$ERR_LOG" 2>&1 && [ -x .venv/bin/pip ]; then
        return 0
    fi

    # (2) pip 抜きで作り、macOS バージョンの補正を入れてから pip を導入する
    warn "pip が macOS のバージョンを取得できず失敗しました。補正を入れて再試行します"
    rm -rf .venv
    "$py" -m venv --without-pip .venv >>"$ERR_LOG" 2>&1 || return 1
    install_macver_fix >>"$ERR_LOG" 2>&1 || return 1
    MACVER_FIX_APPLIED=1
    if .venv/bin/python -m ensurepip --upgrade --default-pip >>"$ERR_LOG" 2>&1 && [ -x .venv/bin/pip ]; then
        return 0
    fi

    # (3) 証明書処理も従来方式に落として再試行
    warn "証明書処理も従来方式に切り替えます"
    export PIP_USE_DEPRECATED=legacy-certs
    PIP_FIX_APPLIED=1
    if .venv/bin/python -m ensurepip --upgrade --default-pip >>"$ERR_LOG" 2>&1 && [ -x .venv/bin/pip ]; then
        return 0
    fi

    # (4) truststore 導入前の pip 24.0 を wheel から直接入れる（pip 不要）
    warn "ensurepip が使えないため、pip 24.0 を直接導入します"
    local whl=".venv/$PIP_WHEEL_NAME"
    if curl -fsSL -o "$whl" "$PIP_WHEEL_URL" >>"$ERR_LOG" 2>&1 &&
       .venv/bin/python "$whl/pip" install "$whl" >>"$ERR_LOG" 2>&1; then
        rm -f "$whl"
        return 0
    fi

    # (5) 最後の手段
    warn "get-pip.py も試します"
    if curl -fsSL https://bootstrap.pypa.io/get-pip.py -o .venv/get-pip.py >>"$ERR_LOG" 2>&1 &&
       .venv/bin/python .venv/get-pip.py >>"$ERR_LOG" 2>&1; then
        rm -f .venv/get-pip.py
        return 0
    fi
    return 1
}

if [ -x .venv/bin/python ] && [ -x .venv/bin/pip ]; then
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
            if [ "$MACVER_FIX_APPLIED" = "1" ]; then
                ok "macOS バージョン取得の補正を適用しました（$(.venv/bin/python -c 'import platform; print(platform.mac_ver()[0] or "不明")')）"
            fi
            [ "$PIP_FIX_APPLIED" = "1" ] && ok "証明書処理を従来方式に切り替えました"
            break
        fi
        warn "$py では作成できませんでした。次の Python を試します"
    done <<< "$PYTHONS"

    if [ -z "$VENV_PY" ]; then
        printf "\n--- 詳細ログ（末尾30行）-------------------------------\n" >&2
        tail -30 "$ERR_LOG" >&2
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
