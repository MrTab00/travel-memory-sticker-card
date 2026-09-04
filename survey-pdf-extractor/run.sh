#!/usr/bin/env bash
# =============================================================================
# 実行用ラッパー（仮想環境を毎回 activate しなくて済むようにするだけ）
#
#   ./run.sh --show-groups
#   ./run.sh --dry-run
#   ./run.sh --only No01
#   ./run.sh
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "セットアップがまだです。先に次を実行してください:" >&2
    echo "    bash setup_mac.sh" >&2
    exit 1
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    # --self-test など API を使わないモードもあるので、警告にとどめる
    printf '\033[0;33m!\033[0m 環境変数 ANTHROPIC_API_KEY が未設定です。API を使う実行は失敗します。\n' >&2
    printf "    export ANTHROPIC_API_KEY='sk-ant-...'\n\n" >&2
fi

exec .venv/bin/python main.py "$@"
