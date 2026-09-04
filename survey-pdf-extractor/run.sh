#!/usr/bin/env bash
# =============================================================================
# 実行用ラッパー
#
#   ./run.sh --show-groups
#   ./run.sh --dry-run
#   ./run.sh --only No01
#   ./run.sh
#
# APIキーは .env ファイルから読み込みます（ターミナルに入力しないので
# コマンド履歴に残りません）。.env は Git にコミットされません。
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

YELLOW=$'\033[0;33m'; RED=$'\033[0;31m'; NC=$'\033[0m'

if [ ! -x .venv/bin/python ]; then
    echo "セットアップがまだです。先に次を実行してください:" >&2
    echo "    bash setup_mac.sh" >&2
    exit 1
fi

# --- .env の権限チェック（本人以外が読める状態なら直す）--------------------
if [ -f .env ]; then
    # macOS は stat -f、Linux は stat -c
    PERM=$(stat -f '%Lp' .env 2>/dev/null || true)
    case "$PERM" in [0-7][0-7][0-7]) ;; *) PERM=$(stat -c '%a' .env 2>/dev/null || echo "") ;; esac
    if [ -n "$PERM" ] && [ "$PERM" != "600" ]; then
        chmod 600 .env 2>/dev/null && \
            printf '%s!%s .env の権限を 600（自分だけ読める）に直しました。\n' "$YELLOW" "$NC" >&2
    fi
fi

# --- キーの有無を確認（--self-test など不要なモードもあるので警告のみ）-----
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    if [ ! -f .env ]; then
        printf '%s!%s APIキーの設定ファイルがありません。次を実行してください:\n' "$YELLOW" "$NC" >&2
        printf '    cp .env.example .env && chmod 600 .env && open -e .env\n\n' >&2
    elif ! grep -qE '^[[:space:]]*(export[[:space:]]+)?ANTHROPIC_API_KEY[[:space:]]*=[[:space:]]*[^[:space:]#]' .env; then
        printf '%s!%s .env に APIキーがまだ書かれていません（API を使う実行は失敗します）。\n' "$YELLOW" "$NC" >&2
        printf '    open -e .env  で開き、ANTHROPIC_API_KEY=sk-ant-... の行を書いてください。\n\n' >&2
    fi
fi

exec .venv/bin/python main.py "$@"
