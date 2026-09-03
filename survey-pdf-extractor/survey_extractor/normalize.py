"""モデルが返した生の値を、集計できる形に正規化・検算する。

モデル任せにせず、ここで選択肢との突き合わせと数値パースを行い、
少しでも怪しい値は confidence を下げて「要確認」シートに必ず出す。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .config import Question

CONFIDENCE_ORDER = {"high": 2, "medium": 1, "low": 0}
STATUS_VALUES = ("answered", "blank", "unreadable")
_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _fold(text: str) -> str:
    """全角/半角・大文字小文字・空白の揺れを吸収した比較用キー。"""
    folded = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", folded).casefold()


def _lower_confidence(current: str, ceiling: str) -> str:
    if CONFIDENCE_ORDER.get(current, 0) <= CONFIDENCE_ORDER[ceiling]:
        return current
    return ceiling


def _match_choice(value: str, choices: tuple[str, ...]) -> tuple[str | None, bool]:
    """選択肢と突き合わせる。戻り値は (一致した選択肢, 表記ゆれ補正をしたか)。"""
    if value in choices:
        return value, False
    folded = {_fold(c): c for c in choices}
    hit = folded.get(_fold(value))
    if hit is not None:
        return hit, True
    return None, False


def _parse_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFKC", value).replace(",", "")
    match = _NUMBER_RE.search(text)
    return float(match.group()) if match else None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _tidy_number(num: float) -> float | int:
    return int(num) if num.is_integer() else num


def normalize_answer(q: Question, raw: Any) -> dict[str, Any]:
    """1設問分の回答を正規化した dict を返す。

    戻り値のキー: value / raw_text / status / confidence / page / flags
    """
    flags: list[str] = []

    if not isinstance(raw, dict):
        return {
            "value": None,
            "raw_text": None if raw is None else str(raw),
            "status": "unreadable",
            "confidence": "low",
            "page": None,
            "flags": ["missing_answer"],
        }

    status = raw.get("status")
    if status not in STATUS_VALUES:
        flags.append(f"invalid_status:{status!r}")
        status = "answered" if raw.get("value") is not None else "unreadable"

    confidence = raw.get("confidence")
    if confidence not in CONFIDENCE_ORDER:
        flags.append(f"invalid_confidence:{confidence!r}")
        confidence = "low"

    raw_text = _clean_text(raw.get("raw_text"))

    page = raw.get("page")
    if isinstance(page, bool) or not isinstance(page, int):
        page = None

    value: Any = raw.get("value")

    # --- 型ごとの正規化 -------------------------------------------------
    if q.type == "single_choice" and value is not None:
        matched, adjusted = _match_choice(str(value), q.choices)
        if matched is None:
            flags.append("choice_mismatch")
            confidence = "low"
            value = str(value)
        else:
            if adjusted:
                flags.append("choice_normalized")
                confidence = _lower_confidence(confidence, "medium")
            value = matched

    elif q.type == "multi_choice" and value is not None:
        if not isinstance(value, list):
            value = [value]
            flags.append("multi_choice_not_list")
            confidence = "low"
        selected: list[str] = []
        for item in value:
            matched, adjusted = _match_choice(str(item), q.choices)
            if matched is None:
                flags.append(f"choice_mismatch:{item}")
                confidence = "low"
                continue
            if adjusted:
                flags.append("choice_normalized")
                confidence = _lower_confidence(confidence, "medium")
            if matched not in selected:
                selected.append(matched)
        value = selected

    elif q.type == "number" and value is not None:
        parsed = _parse_number(value)
        if parsed is None:
            flags.append("number_unparsable")
            confidence = "low"
            if raw_text is None:
                raw_text = str(value)
            value = None
        else:
            value = _tidy_number(parsed)

    elif q.type == "number" and value is None and status == "answered" and raw_text:
        parsed = _parse_number(raw_text)
        if parsed is not None:
            flags.append("number_from_raw_text")
            confidence = _lower_confidence(confidence, "medium")
            value = _tidy_number(parsed)

    elif q.type == "free_text" and value is not None:
        value = _clean_text(value)

    # --- status と value の整合性チェック --------------------------------
    empty = value is None or (isinstance(value, list) and not value)

    if status == "answered" and empty and raw_text is None:
        flags.append("answered_but_empty")
        confidence = "low"
    elif status != "answered" and not empty:
        flags.append(f"value_with_status_{status}")
        confidence = _lower_confidence(confidence, "medium")
    elif status == "unreadable":
        confidence = "low"

    return {
        "value": value,
        "raw_text": raw_text,
        "status": status,
        "confidence": confidence,
        "page": page,
        "flags": flags,
    }


def display_value(q: Question, answer: dict[str, Any]) -> Any:
    """Excel のセルに入れる表示値。"""
    value = answer.get("value")
    if value is None:
        return "" if answer.get("status") == "blank" else "【判読不能】"
    if isinstance(value, list):
        return " / ".join(value) if value else ""
    return value
