"""入力 PDF の列挙・回答者単位への分割・画像フォールバック。"""

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader, PdfWriter

MAX_IMAGE_BYTES = 4_500_000  # 1画像あたりの目安上限（API 制限 5MB に対する安全マージン）
MIN_IMAGE_DPI = 100


class PdfError(Exception):
    """PDF の読み込み・分割に関するエラー。"""


@dataclass
class Respondent:
    """回答者1名分の入力。"""

    id: str
    source_file: Path
    pages: tuple[int, ...]  # 元 PDF 内のページ番号（1始まり）
    pdf_bytes: bytes
    marker: str | None = None  # 用紙右上の手書き番号（検出した場合）

    @property
    def page_start(self) -> int:
        return self.pages[0] if self.pages else 1

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def page_range(self) -> tuple[int, int]:
        return (self.pages[0], self.pages[-1]) if self.pages else (1, 1)

    @property
    def is_contiguous(self) -> bool:
        return list(self.pages) == list(range(self.pages[0], self.pages[-1] + 1))

    @property
    def page_label(self) -> str:
        if self.is_contiguous:
            return f"p{self.pages[0]}-{self.pages[-1]}"
        return "p" + ",".join(str(p) for p in self.pages)


def _natural_key(path: Path) -> list:
    """ファイル名の数字部分を数値として扱うソートキー（file2 < file10）。"""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


def list_input_pdfs(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        raise PdfError(f"入力ディレクトリが見つかりません: {input_dir}")
    pdfs = [p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]
    return sorted(pdfs, key=_natural_key)


def read_page_count(path: Path) -> int:
    try:
        return len(PdfReader(str(path)).pages)
    except Exception as exc:  # pragma: no cover - 破損 PDF
        raise PdfError(f"PDF を読み込めません: {path.name} ({exc})") from exc


def slice_pdf(path: Path, page_numbers: Iterable[int]) -> bytes:
    """指定ページ（1始まり）だけを抜き出した PDF のバイト列を返す。"""
    reader = PdfReader(str(path))
    writer = PdfWriter()
    for number in page_numbers:
        index = number - 1
        if 0 <= index < len(reader.pages):
            writer.add_page(reader.pages[index])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _sanitize_id(name: str) -> str:
    """Excel のシート名・ファイル名として安全な回答者IDにする。"""
    cleaned = re.sub(r'[\\/:*?"<>|\[\]]', "_", name).strip()
    return cleaned or "respondent"


def discover_respondents(
    input_dir: Path,
    layout: str,
    pages_per_respondent: int,
) -> tuple[list[Respondent], list[str]]:
    """入力 PDF を回答者単位に整理する。

    Returns:
        (回答者リスト, 警告メッセージのリスト)
    """
    warnings: list[str] = []
    pdfs = list_input_pdfs(input_dir)
    if not pdfs:
        raise PdfError(f"{input_dir} に PDF が見つかりません。")

    page_counts = {p: read_page_count(p) for p in pdfs}

    resolved = layout
    if layout == "auto":
        if len(pdfs) == 1 and page_counts[pdfs[0]] > pages_per_respondent:
            resolved = "single_file"
        else:
            resolved = "per_respondent"
        warnings.append(f"レイアウト自動判定: {resolved}（PDF {len(pdfs)}件）")

    respondents: list[Respondent] = []

    if resolved == "per_respondent":
        for path in pdfs:
            pages = page_counts[path]
            if pages != pages_per_respondent:
                warnings.append(
                    f"{path.name}: {pages}ページ（想定 {pages_per_respondent}ページ）。"
                    "そのまま1名分として処理します。"
                )
            respondents.append(
                Respondent(
                    id=_sanitize_id(path.stem),
                    source_file=path,
                    pages=tuple(range(1, pages + 1)),
                    pdf_bytes=path.read_bytes(),
                )
            )
    else:  # single_file
        for path in pdfs:
            total = page_counts[path]
            chunks = -(-total // pages_per_respondent)  # 切り上げ
            if total % pages_per_respondent:
                warnings.append(
                    f"{path.name}: 全{total}ページが {pages_per_respondent} で割り切れません。"
                    f"最後の1名分は {total % pages_per_respondent} ページになります。"
                    "（右上の手書き番号があるなら layout: by_page_marker を推奨）"
                )
            for i in range(chunks):
                start = i * pages_per_respondent
                count = min(pages_per_respondent, total - start)
                numbers = tuple(range(start + 1, start + count + 1))
                respondents.append(
                    Respondent(
                        id=_sanitize_id(f"{path.stem}_{i + 1:02d}"),
                        source_file=path,
                        pages=numbers,
                        pdf_bytes=slice_pdf(path, numbers),
                    )
                )

    seen: set[str] = set()
    for r in respondents:
        if r.id in seen:
            raise PdfError(f"回答者IDが重複しています: {r.id}（入力ファイル名を見直してください）")
        seen.add(r.id)

    return respondents, warnings


def pdf_document_block(pdf_bytes: bytes) -> dict:
    """PDF をそのまま送るための document ブロック。"""
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
        },
    }


def render_page_images(pdf_bytes: bytes, dpi: int = 300) -> list[dict]:
    """画像フォールバック: 各ページを PNG 化して image ブロックのリストを返す。

    ページ番号を見失わないよう、各画像の直前にページ番号のテキストブロックを挟む。
    """
    try:
        import pymupdf  # type: ignore
    except ImportError:  # pragma: no cover - 古い PyMuPDF
        import fitz as pymupdf  # type: ignore

    blocks: list[dict] = []
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page_index, page in enumerate(doc, start=1):
            current_dpi = dpi
            while True:
                zoom = current_dpi / 72
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
                data = pix.tobytes("png")
                if len(data) <= MAX_IMAGE_BYTES or current_dpi <= MIN_IMAGE_DPI:
                    break
                current_dpi = max(MIN_IMAGE_DPI, int(current_dpi * 0.7))
            blocks.append({"type": "text", "text": f"=== {page_index}ページ目 ==="})
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.standard_b64encode(data).decode("ascii"),
                    },
                }
            )
    return blocks
