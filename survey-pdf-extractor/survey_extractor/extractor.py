"""Anthropic API を呼び出して 1名分の回答を抽出する。"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import anthropic

from .config import Config
from .normalize import normalize_answer
from .pdf_utils import Respondent, pdf_document_block, render_page_images
from .prompts import SYSTEM_PROMPT, build_user_instruction
from .schema import RESPONDENT_NAME_KEY, TOOL_NAME, build_tool

SCHEMA_VERSION = 1
MAX_RETRIES = 3  # API エラー時のリトライ回数（初回試行は含まない）
BASE_DELAY = 2.0
MAX_PARAM_FALLBACKS = 5

# 「PDF が大きすぎる／ページが多すぎる」系のエラー文言
_SIZE_HINTS = ("too large", "exceed", "maximum", "too many pages", "page limit", "size limit")


class ExtractionError(Exception):
    """1名分の抽出に失敗した。"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _usage_dict(usage: Any) -> dict[str, int]:
    keys = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    return {k: int(getattr(usage, k, 0) or 0) for k in keys}


class Extractor:
    """設定を保持し、回答者ごとに API を呼び出す。"""

    def __init__(
        self,
        config: Config,
        client: anthropic.Anthropic | None = None,
        force_images: bool = False,
        log: Callable[[str], None] = print,
    ) -> None:
        self.config = config
        # リトライはこちらで指数バックオフを実装するため SDK 側は無効化する
        self.client = client or anthropic.Anthropic(max_retries=0)
        self.force_images = force_images
        self.log = log

        self._strict = config.model.strict_tools
        self._use_temperature = config.model.temperature is not None
        self._send_thinking = config.model.thinking == "disabled"
        self._use_effort = config.model.effort is not None
        self._force_tool = True

    # ------------------------------------------------------------------
    # リクエスト組み立て
    # ------------------------------------------------------------------
    def _tool(self) -> dict[str, Any]:
        return build_tool(self.config, strict=self._strict)

    def _content_blocks(self, respondent: Respondent, mode: str) -> list[dict[str, Any]]:
        instruction = build_user_instruction(self.config, respondent.page_count)
        if mode == "pdf":
            return [pdf_document_block(respondent.pdf_bytes), {"type": "text", "text": instruction}]
        images = render_page_images(respondent.pdf_bytes, dpi=self.config.model.image_dpi)
        return [*images, {"type": "text", "text": instruction}]

    def _params(self, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        m = self.config.model
        params: dict[str, Any] = {
            "model": m.name,
            "max_tokens": m.max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": blocks}],
            "tools": [self._tool()],
        }
        if self._force_tool:
            params["tool_choice"] = {"type": "tool", "name": TOOL_NAME}
        if self._send_thinking:
            params["thinking"] = {"type": "disabled"}
        if self._use_effort:
            params["output_config"] = {"effort": m.effort}
        if self._use_temperature:
            params["temperature"] = m.temperature
        return params

    # ------------------------------------------------------------------
    # API 呼び出し（リトライ＋パラメータ互換フォールバック）
    # ------------------------------------------------------------------
    def _adapt_to_bad_request(self, message: str) -> str | None:
        """400 の内容に応じてリクエストを調整する。調整内容の説明を返す。"""
        text = message.lower()
        if self._use_temperature and "temperature" in text:
            self._use_temperature = False
            return "temperature は使用できないため送信を取りやめました"
        if self._send_thinking and "thinking" in text:
            self._send_thinking = False
            return "thinking パラメータを取り下げました"
        if self._use_effort and ("effort" in text or "output_config" in text):
            self._use_effort = False
            return "output_config.effort を取り下げました"
        if self._strict and ("strict" in text or "schema" in text):
            self._strict = False
            return "tool の strict モードを無効化しました"
        if self._force_tool and "tool_choice" in text:
            self._force_tool = False
            return "tool_choice の強制指定を auto に戻しました"
        return None

    def _is_size_error(self, message: str) -> bool:
        text = message.lower()
        return any(hint in text for hint in _SIZE_HINTS)

    def _request(self, respondent: Respondent, mode: str) -> tuple[Any, str]:
        """1名分のリクエストを送る。戻り値は (レスポンス, 実際に使った mode)。"""
        attempt = 0
        adjustments = 0
        # リトライのたびに base64 化・画像化をやり直さないようにキャッシュする
        blocks_cache: dict[str, list[dict[str, Any]]] = {}

        while True:
            if mode not in blocks_cache:
                blocks_cache[mode] = self._content_blocks(respondent, mode)
            params = self._params(blocks_cache[mode])
            try:
                return self.client.messages.create(**params), mode

            except anthropic.BadRequestError as exc:
                message = str(getattr(exc, "message", "") or exc)
                if mode == "pdf" and self._is_size_error(message):
                    self.log(f"    PDF 直送を拒否されたため画像フォールバックに切替: {message[:120]}")
                    mode = "images"
                    continue
                adjustment = self._adapt_to_bad_request(message)
                if adjustment and adjustments < MAX_PARAM_FALLBACKS:
                    adjustments += 1
                    self.log(f"    API に合わせて調整: {adjustment}")
                    continue
                raise ExtractionError(f"リクエストが拒否されました: {message}") from exc

            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
                raise ExtractionError(
                    f"認証エラー: {exc}. 環境変数 ANTHROPIC_API_KEY を確認してください。"
                ) from exc

            except anthropic.NotFoundError as exc:
                raise ExtractionError(
                    f"モデルまたはエンドポイントが見つかりません: {exc}. "
                    "questions.yaml の model.name を確認してください。"
                ) from exc

            except (
                anthropic.RateLimitError,
                anthropic.APIConnectionError,
                anthropic.APITimeoutError,
                anthropic.APIStatusError,
            ) as exc:
                if isinstance(exc, anthropic.APIStatusError) and not isinstance(
                    exc, anthropic.RateLimitError
                ):
                    if exc.status_code == 413 and mode == "pdf":
                        self.log("    リクエストが大きすぎるため画像フォールバックに切替")
                        mode = "images"
                        continue
                    if exc.status_code < 500:
                        raise ExtractionError(f"API エラー ({exc.status_code}): {exc}") from exc
                attempt += 1
                if attempt > MAX_RETRIES:
                    raise ExtractionError(f"{MAX_RETRIES}回リトライしても失敗しました: {exc}") from exc
                delay = BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 1)
                self.log(f"    API エラー（{type(exc).__name__}）。{delay:.1f}秒後にリトライ {attempt}/{MAX_RETRIES}")
                time.sleep(delay)

    # ------------------------------------------------------------------
    # 抽出本体
    # ------------------------------------------------------------------
    def extract(self, respondent: Respondent) -> dict[str, Any]:
        mode = "images" if self.force_images else "pdf"
        if mode == "pdf" and len(respondent.pdf_bytes) > self.config.model.pdf_max_bytes:
            self.log(
                f"    PDF が {len(respondent.pdf_bytes) / 1e6:.1f}MB のため画像フォールバックを使用します"
            )
            mode = "images"

        response, used_mode = self._request(respondent, mode)

        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            raise ExtractionError(f"モデルが応答を拒否しました: {detail}")
        if response.stop_reason == "max_tokens":
            raise ExtractionError(
                "max_tokens に達して出力が途中で切れました。"
                "questions.yaml の model.max_tokens を増やしてください。"
            )

        tool_block = next(
            (b for b in response.content if b.type == "tool_use" and b.name == TOOL_NAME), None
        )
        if tool_block is None:
            texts = " ".join(b.text for b in response.content if b.type == "text")
            raise ExtractionError(
                f"ツール呼び出しが返りませんでした (stop_reason={response.stop_reason}): {texts[:300]}"
            )

        data = tool_block.input
        if isinstance(data, str):  # 念のため（SDK は dict を返す）
            data = json.loads(data)

        answers = {q.id: normalize_answer(q, data.get(q.id)) for q in self.config.questions}
        respondent_name = data.get(RESPONDENT_NAME_KEY)

        return {
            "schema_version": SCHEMA_VERSION,
            "respondent_id": respondent.id,
            "respondent_name": respondent_name if isinstance(respondent_name, str) else None,
            "source_file": respondent.source_file.name,
            "page_start": respondent.page_start,
            "page_count": respondent.page_count,
            "model": response.model,
            "input_mode": used_mode,
            "extracted_at": _utcnow(),
            "usage": _usage_dict(response.usage),
            "answers": answers,
        }

    # ------------------------------------------------------------------
    # 見積り
    # ------------------------------------------------------------------
    def count_tokens(self, respondent: Respondent) -> int:
        """実行前の入力トークン数見積り（課金なし）。"""
        mode = "images" if self.force_images else "pdf"
        result = self.client.messages.count_tokens(
            model=self.config.model.name,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": self._content_blocks(respondent, mode)}],
            tools=[self._tool()],
        )
        return int(result.input_tokens)


def save_record(record: dict[str, Any], raw_dir: Path) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{record['respondent_id']}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_records(raw_dir: Path) -> list[dict[str, Any]]:
    """raw/*.json を回答者ID順に読み込む。"""
    if not raw_dir.exists():
        return []
    records = []
    for path in sorted(raw_dir.glob("*.json")):
        with path.open(encoding="utf-8") as f:
            records.append(json.load(f))
    return records
