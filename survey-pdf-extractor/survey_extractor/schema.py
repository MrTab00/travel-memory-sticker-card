"""設問定義から tool use (function calling) のスキーマを組み立てる。

プロンプトで「JSON だけ返して」と指示する方式は前置きが混入するため使わない。
tool_choice で下記ツールの呼び出しを強制し、input_schema で構造を縛る。
"""

from __future__ import annotations

from typing import Any

from .config import Config, Question

TOOL_NAME = "record_survey_answers"
RESPONDENT_NAME_KEY = "_respondent_name"

STATUS_VALUES = ("answered", "blank", "unreadable")
CONFIDENCE_VALUES = ("high", "medium", "low")


def _value_schema(q: Question) -> dict[str, Any]:
    if q.type == "single_choice":
        return {
            "type": ["string", "null"],
            "enum": [*q.choices, None],
            "description": (
                "選択された選択肢の文字列（choices と完全一致させること）。"
                "未記入・判読不能の場合は null。"
            ),
        }
    if q.type == "multi_choice":
        return {
            "type": ["array", "null"],
            "items": {"type": "string", "enum": list(q.choices)},
            "description": (
                "選択された選択肢の配列（choices と完全一致させること）。"
                "1つも選択されていなければ空配列、判読不能なら null。"
            ),
        }
    if q.type == "rating":
        labels = "、".join(q.scale_label(v) for v in q.scale_values)
        return {
            "type": ["integer", "null"],
            "enum": [*q.scale_values, None],
            "description": (
                f"丸（○）が付けられている数字（{q.scale_min}〜{q.scale_max}）。{labels}。"
                "丸の位置を数字そのもので判断し、選択肢の文言から推測しないこと。"
                "未記入・判読不能・どれに丸が付いているか特定できない場合は null。"
            ),
        }
    if q.type == "number":
        unit = f"（単位: {q.unit}）" if q.unit else ""
        return {
            "type": ["number", "null"],
            "description": (
                f"半角数字に正規化した数値{unit}。"
                "範囲や複数値が書かれている場合は代表値を入れ、原文は raw_text に残すこと。"
                "未記入・判読不能の場合は null。"
            ),
        }
    return {
        "type": ["string", "null"],
        "description": (
            "記入内容の全文。要約・言い換え・誤字修正をしないこと。"
            "未記入・判読不能の場合は null。"
        ),
    }


def _answer_schema(q: Question) -> dict[str, Any]:
    if q.is_rating:
        choices = f" / 尺度: {' | '.join(q.scale_label(v) for v in q.scale_values)}"
    else:
        choices = f" / 選択肢: {' | '.join(q.choices)}" if q.choices else ""
    note = f" / 注意: {q.note}" if q.note else ""
    return {
        "type": "object",
        "description": f"{q.id} [{q.type}] {q.text}{choices}{note}",
        "properties": {
            "value": _value_schema(q),
            "raw_text": {
                "type": ["string", "null"],
                "description": (
                    "実際に紙面から読み取った文字列を原文ママで（手書きの誤字・記号もそのまま）。"
                    "選択式ではチェックの付き方（例: 『検討中に丸』）を書いてよい。"
                    "何も書かれていない場合は null。"
                ),
            },
            "status": {
                "type": "string",
                "enum": list(STATUS_VALUES),
                "description": (
                    "answered=回答あり / blank=未記入（空欄） / unreadable=記入はあるが判読不能。"
                    "空欄と判読不能を必ず区別すること。"
                ),
            },
            "confidence": {
                "type": "string",
                "enum": list(CONFIDENCE_VALUES),
                "description": (
                    "読み取りの確信度。high=明確に読める / medium=文脈から判断した箇所がある / "
                    "low=推測が入る・判読困難。少しでも迷ったら medium 以下にすること。"
                ),
            },
            "page": {
                "type": ["integer", "null"],
                "description": "この設問が記載されていたページ番号（この回答者の PDF 内で1始まり）。",
            },
        },
        "required": ["value", "raw_text", "status", "confidence", "page"],
        "additionalProperties": False,
    }


def build_tool(config: Config, strict: bool = True) -> dict[str, Any]:
    """1名分の回答を記録するツール定義を返す。"""
    properties: dict[str, Any] = {
        RESPONDENT_NAME_KEY: {
            "type": ["string", "null"],
            "description": (
                "表紙などに会社名・回答者名が記載されていればその文字列。なければ null。"
            ),
        }
    }
    for q in config.questions:
        properties[q.id] = _answer_schema(q)

    tool: dict[str, Any] = {
        "name": TOOL_NAME,
        "description": (
            f"「{config.meta.survey_name}」1名分の回答をすべて構造化して記録する。"
            "全設問について必ず1件ずつ、読み取れなかったものも含めて返すこと。"
        ),
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": [RESPONDENT_NAME_KEY, *(q.id for q in config.questions)],
            "additionalProperties": False,
        },
    }
    if strict:
        tool["strict"] = True
    return tool
