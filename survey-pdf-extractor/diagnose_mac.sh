#!/usr/bin/env bash
# =============================================================================
# 環境診断（1回実行して、出力をそのまま貼るだけ）
#
#   bash diagnose_mac.sh
#
# 何も変更しません。読み取りと一時フォルダでの試行のみ。
# =============================================================================
set -u

line() { printf '\n----- %s -----\n' "$1"; }

echo "==================== 環境診断 ===================="
echo "日時: $(date '+%Y-%m-%d %H:%M:%S')"
echo "シェル: ${SHELL:-?}   ユーザー: $(id -un)"

line "1. macOS のバージョン取得（3通り）"
echo "[sw_vers]"
sw_vers 2>&1 | sed 's/^/  /' || echo "  ← 失敗"
echo "[sysctl kern.osproductversion]"
sysctl -n kern.osproductversion 2>&1 | sed 's/^/  /' || echo "  ← 失敗"
echo "[SystemVersion.plist]"
ls -l /System/Library/CoreServices/SystemVersion.plist 2>&1 | sed 's/^/  /'

line "2. Python の候補"
for c in python3 python3.13 python3.12 python3.11 python3.10 \
         /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
         /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3
do
    if command -v "$c" >/dev/null 2>&1; then
        printf '  %-62s %s\n' "$c" "$("$c" -V 2>&1)"
    fi
done

# 診断対象の Python（引数で指定可）
PY="${1:-}"
if [ -z "$PY" ]; then
    for c in python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$c" >/dev/null 2>&1 &&
           "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null; then
            PY="$c"; break
        fi
    done
fi
[ -n "$PY" ] || { echo "使える Python が見つかりません"; exit 1; }

line "3. mac_ver が空になる原因の切り分け（対象: $PY）"
"$PY" - <<'PYEOF' 2>&1 | sed 's/^/  /'
import os, sys

print("実行中の python :", sys.version.split()[0], "|", sys.executable)
print("sys.platform    :", sys.platform)

fn = "/System/Library/CoreServices/SystemVersion.plist"

# CPython の platform.mac_ver() は、次の2つのどちらかでのみ空を返す
print("[原因1] plist が見える :", os.path.exists(fn))

try:
    import plistlib
    print("[原因2] import plistlib : OK")
    try:
        with open(fn, "rb") as f:
            pl = plistlib.load(f)
        print("        ProductVersion  :", repr(pl.get("ProductVersion")))
    except Exception as exc:
        print("        plist 読み取り失敗:", type(exc).__name__, exc)
except Exception as exc:
    print("[原因2] import plistlib : 失敗 →", type(exc).__name__, exc)
    for mod in ("xml", "xml.parsers", "xml.parsers.expat", "pyexpat"):
        try:
            __import__(mod)
            print("        ", mod, ": OK")
        except Exception as e2:
            print("        ", mod, ": 失敗 →", type(e2).__name__, e2)

import platform
print("platform.mac_ver()      :", platform.mac_ver())
print("platform モジュールの場所:", platform.__file__)
print("platform が標準ライブラリか:", platform.__file__.startswith(sys.prefix) or "/lib/python" in platform.__file__)
PYEOF

line "4. 関係しそうな環境変数"
for v in PYTHONPATH PYTHONHOME PYTHONSTARTUP SYSTEM_VERSION_COMPAT \
         MACOSX_DEPLOYMENT_TARGET PIP_USE_DEPRECATED PIP_CONFIG_FILE \
         SSL_CERT_FILE REQUESTS_CA_BUNDLE VIRTUAL_ENV
do
    eval "val=\${$v:-（未設定）}"
    printf '  %-28s %s\n' "$v" "$val"
done
echo "  pip 設定ファイル:"
for f in ~/.pip/pip.conf ~/Library/Application\ Support/pip/pip.conf ~/.config/pip/pip.conf /etc/pip.conf; do
    [ -f "$f" ] && { echo "    $f:"; sed 's/^/      /' "$f"; }
done
[ -f ~/.pydistutils.cfg ] && { echo "    ~/.pydistutils.cfg:"; sed 's/^/      /' ~/.pydistutils.cfg; }

line "5. venv 作成を段階ごとに試す（/tmp で実施・既存環境に影響なし）"
WORK=$(mktemp -d /tmp/venvdiag.XXXXXX)
trap 'rm -rf "$WORK"' EXIT
echo "  作業場所: $WORK"

echo
echo "  [A] 素の venv"
"$PY" -m venv "$WORK/a" > "$WORK/a.log" 2>&1
echo "      終了コード: $?   pip: $([ -x "$WORK/a/bin/pip" ] && echo あり || echo なし)"
[ -s "$WORK/a.log" ] && { echo "      エラー末尾:"; tail -4 "$WORK/a.log" | sed 's/^/        /'; }

echo
echo "  [B] --without-pip"
"$PY" -m venv --without-pip "$WORK/b" > "$WORK/b.log" 2>&1
RC=$?
echo "      終了コード: $RC"
[ -s "$WORK/b.log" ] && { echo "      エラー末尾:"; tail -4 "$WORK/b.log" | sed 's/^/        /'; }

if [ -x "$WORK/b/bin/python" ]; then
    echo
    echo "  [C] site-packages の場所を取得"
    SITE=$("$WORK/b/bin/python" -c 'import site; print(site.getsitepackages()[0])' 2>&1)
    echo "      $SITE"

    if [ -d "$SITE" ]; then
        echo
        echo "  [D] mac_ver 補正 (.pth) を入れて効くか"
        cat > "$SITE/_diagfix.py" <<'PYEOF'
import sys
if sys.platform == "darwin":
    import platform, subprocess
    if not platform.mac_ver()[0]:
        v = ""
        try:
            v = subprocess.run(["/usr/bin/sw_vers", "-productVersion"],
                               capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            pass
        if v:
            m = platform.machine() or "x86_64"
            platform.mac_ver = lambda *a, **k: (v, ("", "", ""), m)
PYEOF
        echo "import _diagfix" > "$SITE/zz_diagfix.pth"
        echo "      補正後の mac_ver: $("$WORK/b/bin/python" -c 'import platform; print(platform.mac_ver())' 2>&1)"

        echo
        echo "  [E] 補正を入れた状態で ensurepip"
        "$WORK/b/bin/python" -m ensurepip --upgrade --default-pip > "$WORK/e.log" 2>&1
        echo "      終了コード: $?   pip: $([ -x "$WORK/b/bin/pip" ] && "$WORK/b/bin/pip" --version 2>&1 | head -1 || echo なし)"
        [ -s "$WORK/e.log" ] && { echo "      ログ末尾:"; tail -6 "$WORK/e.log" | sed 's/^/        /'; }

        if [ -x "$WORK/b/bin/pip" ]; then
            echo
            echo "  [F] 実際に1つインストールしてみる"
            "$WORK/b/bin/pip" install --quiet PyYAML > "$WORK/f.log" 2>&1
            echo "      終了コード: $?"
            tail -6 "$WORK/f.log" | sed 's/^/        /'
        fi
    fi
fi

line "6. uv（pip を使わない代替手段）が使えるか"
if command -v uv >/dev/null 2>&1; then
    echo "  導入済み: $(uv --version 2>&1)"
elif [ -x "$HOME/.local/bin/uv" ]; then
    echo "  導入済み: $("$HOME/.local/bin/uv" --version 2>&1)"
else
    echo "  未導入（必要なら後で入れます）"
fi
echo "  astral.sh への到達性:"
curl -fsSI --max-time 10 https://astral.sh/uv/install.sh -o /dev/null -w "    HTTP %{http_code}\n" 2>&1 || echo "    到達できず"

echo
echo "==================== 診断ここまで ===================="
echo "この出力を全部そのままコピーして送ってください。"
