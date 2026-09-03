"""API を呼ばずにパイプライン（正規化→集計→Excel）を検証するためのダミーデータ生成。

顧客データを使わずに「Excel が正しく作られるか」「要確認シートに低信頼度が並ぶか」を
確認するための機能。`python main.py --self-test` から呼ばれる。
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any

from .config import Config
from .normalize import normalize_answer


def _fake_raw_answer(q, rng: random.Random, page: int) -> dict[str, Any]:
    """モデルが返しそうな生の回答（正常系・空欄・判読不能・表記ゆれ）を作る。"""
    roll = rng.random()

    if roll < 0.08:  # 未記入
        return {"value": None, "raw_text": None, "status": "blank", "confidence": "high", "page": page}
    if roll < 0.16:  # 判読不能
        return {
            "value": None,
            "raw_text": "□□□（判読不能）",
            "status": "unreadable",
            "confidence": "low",
            "page": page,
        }

    confidence = rng.choice(["high", "high", "medium", "low"])

    if q.type == "single_choice":
        value = rng.choice(list(q.choices))
        if roll > 0.93:  # 選択肢にない書き込み（choice_mismatch を発生させる）
            value = "その他（自社基準で対応中）"
        return {
            "value": value,
            "raw_text": f"{value} に丸",
            "status": "answered",
            "confidence": confidence,
            "page": page,
        }

    if q.type == "multi_choice":
        picked = rng.sample(list(q.choices), k=rng.randint(1, len(q.choices)))
        return {
            "value": picked,
            "raw_text": "レ点: " + "、".join(picked),
            "status": "answered",
            "confidence": confidence,
            "page": page,
        }

    if q.type == "number":
        n = rng.randint(1, 200)
        if roll > 0.9:  # 全角・単位付きの手書き（正規化の確認）
            return {
                "value": None,
                "raw_text": f"約{n}台",
                "status": "answered",
                "confidence": "medium",
                "page": page,
            }
        return {"value": n, "raw_text": str(n), "status": "answered", "confidence": confidence, "page": page}

    text = rng.choice(
        [
            "社内にセキュリティ要件を判断できる人材がいない。",
            "サプライヤからSBOMが出てこない。取得の交渉に時間がかかっている。",
            "CRAとJC-STARで要求が重複しており、どちらを先に進めるべきか判断できない。",
            "脆弱性報告の受付窓口が未整備。設計部門と保守部門の役割分担も未定。",
        ]
    )
    return {"value": text, "raw_text": text, "status": "answered", "confidence": confidence, "page": page}


def build_dummy_records(config: Config, count: int = 5, seed: int = 42) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    records = []
    for i in range(1, count + 1):
        answers = {}
        for index, q in enumerate(config.questions):
            page = min(index // 2 + 1, config.meta.pages_per_respondent)
            answers[q.id] = normalize_answer(q, _fake_raw_answer(q, rng, page))
        records.append(
            {
                "schema_version": 1,
                "respondent_id": f"selftest_{i:02d}",
                "respondent_name": f"テスト株式会社{i:02d}",
                "source_file": "selftest.pdf",
                "page_start": (i - 1) * config.meta.pages_per_respondent + 1,
                "page_count": config.meta.pages_per_respondent,
                "model": "(self-test / API 未使用)",
                "input_mode": "selftest",
                "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "answers": answers,
            }
        )
    return records
