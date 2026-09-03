"""用紙の右上に手書きされた回答者番号を読み取り、ページを回答者単位にまとめる。

固定ページ数での機械的な分割は、1名分がページ抜け・重複・順番違いになった瞬間に
以降の全員がずれる。手書き番号でグループ化すれば、そのリスクがなくなり、
「6ページ揃っているか」の検算にもなる。

右上の隅だけを切り出して送るので、1ページあたりのトークンはごくわずか。
"""

from __future__ import annotations

import base64
import json
import re
import unicodedata
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .pdf_utils import Respondent, slice_pdf

MARKER_TOOL_NAME = "record_page_markers"
BATCH_SIZE = 25          # 1リクエストに載せる隅画像の枚数
CORNER_DPI = 200
CACHE_FILENAME = "page_markers.json"

SYSTEM_PROMPT = """\
あなたはスキャンした紙アンケートの整理を担当しています。
各画像は用紙の「右上の隅」だけを切り出したものです。
そこに手書きされた回答者番号（通常は 1、2、3 … のような数字）を読み取ってください。

ルール:
- 印刷された文字やページ番号ではなく、手書きの番号だけを読むこと。
- 数字は半角で返すこと（例: "12"）。数字以外の記号や文字も書かれていればそのまま返す。
- 何も書かれていない場合は marker を null、confidence は "high" とする。
- 書かれているが読めない場合は marker を null、confidence を "low" とする。
- 推測で番号を作らないこと。1 と 7、4 と 9 など紛らわしい場合は confidence を下げる。
"""


class MarkerError(Exception):
    """番号の読み取り・グループ化に失敗した。"""


def build_marker_tool() -> dict[str, Any]:
    return {
        "name": MARKER_TOOL_NAME,
        "description": "各ページ右上の手書き回答者番号を記録する。渡された全ページ分を必ず返すこと。",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "pages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "page": {"type": "integer", "description": "ページ番号（提示したもの）"},
                            "marker": {
                                "type": ["string", "null"],
                                "description": "手書きの回答者番号。無ければ null。",
                            },
                            "confidence": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                            },
                        },
                        "required": ["page", "marker", "confidence"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["pages"],
            "additionalProperties": False,
        },
    }


def corner_blocks(
    pdf_path: Path,
    page_numbers: list[int],
    region: tuple[float, float, float, float],
    dpi: int = CORNER_DPI,
) -> list[dict[str, Any]]:
    """指定ページの右上隅を切り出した content ブロック（ラベル＋画像）を返す。"""
    try:
        import pymupdf  # type: ignore
    except ImportError:  # pragma: no cover
        import fitz as pymupdf  # type: ignore

    x0, y0, x1, y1 = region
    blocks: list[dict[str, Any]] = []
    with pymupdf.open(str(pdf_path)) as doc:
        for number in page_numbers:
            page = doc[number - 1]
            rect = page.rect
            clip = pymupdf.Rect(
                rect.x0 + rect.width * x0,
                rect.y0 + rect.height * y0,
                rect.x0 + rect.width * x1,
                rect.y0 + rect.height * y1,
            )
            pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), clip=clip)
            blocks.append({"type": "text", "text": f"page={number}"})
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.standard_b64encode(pix.tobytes("png")).decode("ascii"),
                    },
                }
            )
    return blocks


def normalize_marker(value: Any) -> str | None:
    """'０１ ' → '1' のように表記を揃える。数字なら先頭の 0 を落とす。"""
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    text = re.sub(r"^(?:no\.?|#|№)\s*", "", text, flags=re.IGNORECASE)
    text = text.strip(".,、。:：-_/ ")
    if not text:
        return None
    return str(int(text)) if text.isdigit() else text


class MarkerDetector:
    """右上の番号を読み取るための API 呼び出し（結果は JSON にキャッシュする）。"""

    def __init__(self, config: Config, client: Any, log: Callable[[str], None] = print) -> None:
        self.config = config
        self.client = client
        self.log = log

    def _call(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "model": self.config.model.name,
            "max_tokens": 4000,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        *blocks,
                        {
                            "type": "text",
                            "text": (
                                "上記の各画像について、右上に手書きされた回答者番号を "
                                f"{MARKER_TOOL_NAME} ツールで返してください。"
                                "提示したページを1つも飛ばさないこと。"
                            ),
                        },
                    ],
                }
            ],
            "tools": [build_marker_tool()],
            "tool_choice": {"type": "tool", "name": MARKER_TOOL_NAME},
        }
        if self.config.model.thinking == "disabled":
            params["thinking"] = {"type": "disabled"}

        response = self.client.messages.create(**params)
        block = next(
            (b for b in response.content if b.type == "tool_use" and b.name == MARKER_TOOL_NAME),
            None,
        )
        if block is None:
            raise MarkerError(f"番号読み取りでツール呼び出しが返りませんでした（{response.stop_reason}）")
        data = block.input
        if isinstance(data, str):
            data = json.loads(data)
        return list(data.get("pages") or [])

    def detect(self, pdf_path: Path, total_pages: int) -> dict[int, dict[str, Any]]:
        """{ページ番号: {marker, confidence}} を返す。"""
        region = self.config.meta.marker_region
        results: dict[int, dict[str, Any]] = {}
        pages = list(range(1, total_pages + 1))

        for start in range(0, len(pages), BATCH_SIZE):
            batch = pages[start : start + BATCH_SIZE]
            self.log(f"  右上の番号を読み取り中: {batch[0]}〜{batch[-1]}ページ / 全{total_pages}ページ")
            blocks = corner_blocks(pdf_path, batch, region)
            for entry in self._call(blocks):
                page = entry.get("page")
                if isinstance(page, int) and page in batch:
                    results[page] = {
                        "marker": normalize_marker(entry.get("marker")),
                        "confidence": entry.get("confidence", "low"),
                    }
            missing = [p for p in batch if p not in results]
            if missing:
                self.log(f"    ※ 返ってこなかったページ: {missing}")
                for page in missing:
                    results[page] = {"marker": None, "confidence": "low"}

        return dict(sorted(results.items()))


# ---------------------------------------------------------------------------
# グループ化
# ---------------------------------------------------------------------------
def group_by_marker(
    markers: dict[int, dict[str, Any]],
    pages_per_respondent: int,
) -> tuple[OrderedDict[str, list[int]], list[str]]:
    """ページ番号→番号 の対応から、{番号: [ページ...]} を作る。

    番号を読めなかったページは、直前のページと同じ回答者として引き継ぐ
    （用紙の途中で番号が薄れているケースを救うため）。必ず警告を出す。
    """
    warnings: list[str] = []
    groups: OrderedDict[str, list[int]] = OrderedDict()
    current: str | None = None

    for page in sorted(markers):
        info = markers[page]
        marker = info.get("marker")
        if marker is None:
            if current is None:
                warnings.append(f"{page}ページ: 右上の番号が読めず、直前のグループもありません。")
                marker = "不明"
            else:
                warnings.append(f"{page}ページ: 右上の番号が読めないため、直前と同じ「{current}」に含めました。")
                marker = current
        elif info.get("confidence") == "low":
            warnings.append(f"{page}ページ: 右上の番号「{marker}」の読み取りが不確実です。")

        groups.setdefault(marker, []).append(page)
        current = marker

    for marker, pages in groups.items():
        if len(pages) != pages_per_respondent:
            warnings.append(
                f"番号「{marker}」: {len(pages)}ページ（想定 {pages_per_respondent}ページ）。"
                f"該当ページ: {pages}"
            )
        if pages != list(range(pages[0], pages[-1] + 1)):
            warnings.append(f"番号「{marker}」: ページが連続していません（{pages}）。順番をご確認ください。")

    return groups, warnings


def respondents_from_groups(
    pdf_path: Path,
    groups: OrderedDict[str, list[int]],
    prefix: str = "No",
) -> list[Respondent]:
    respondents = []
    for marker, pages in groups.items():
        suffix = f"{int(marker):02d}" if marker.isdigit() else marker
        respondents.append(
            Respondent(
                id=f"{prefix}{suffix}",
                source_file=pdf_path,
                pages=tuple(pages),
                pdf_bytes=slice_pdf(pdf_path, pages),
                marker=marker,
            )
        )
    return respondents


# ---------------------------------------------------------------------------
# キャッシュ
# ---------------------------------------------------------------------------
def cache_path(output_dir: Path, pdf_path: Path) -> Path:
    return output_dir / f"{pdf_path.stem}_{CACHE_FILENAME}"


def load_cache(path: Path) -> dict[int, dict[str, Any]] | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    return {int(k): v for k, v in raw.items()}


def save_cache(path: Path, markers: dict[int, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({str(k): v for k, v in markers.items()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
