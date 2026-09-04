"""`.env` ファイルから APIキーを読み込む。

ターミナルに `export ANTHROPIC_API_KEY=...` と打つとコマンド履歴
（~/.zsh_history など）にキーが残ってしまうため、ファイル経由で渡せるようにする。

- 既に環境変数が設定されている場合はそちらを優先し、.env では上書きしない
- `source` は使わず自前で解析する（.env に書かれた文字列が実行されることはない）
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

ENV_FILENAME = ".env"
KEY_NAME = "ANTHROPIC_API_KEY"


def parse_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE 形式を解析する。コメント（#）と空行は無視。"""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip().strip('"').strip("'").strip()
        if key:
            values[key] = value
    return values


def load_env_file(directory: Path | None = None) -> list[str]:
    """`.env` を読み、まだ設定されていない環境変数だけを設定する。

    Returns:
        設定したキー名のリスト（何も設定しなかった場合は空）
    """
    base = directory or Path(__file__).resolve().parent.parent
    path = base / ENV_FILENAME
    if not path.is_file():
        return []

    loaded = []
    for key, value in parse_env_file(path).items():
        if value and not os.environ.get(key):
            os.environ[key] = value
            loaded.append(key)
    return loaded


def env_file_path(directory: Path | None = None) -> Path:
    base = directory or Path(__file__).resolve().parent.parent
    return base / ENV_FILENAME


def is_world_readable(path: Path) -> bool:
    """本人以外も読める権限になっているか（Windows では常に False 扱い）。"""
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    if os.name == "nt":
        return False
    return bool(mode & (stat.S_IRGRP | stat.S_IROTH))


def mask(value: str) -> str:
    """ログ表示用にキーを伏せる（先頭7文字と末尾4文字だけ残す）。"""
    if len(value) <= 14:
        return "*" * len(value)
    return f"{value[:7]}...{value[-4:]}"
