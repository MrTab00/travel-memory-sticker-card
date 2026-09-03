"""questions.yaml の読み込みとバリデーション。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

QUESTION_TYPES = ("single_choice", "multi_choice", "free_text", "number", "rating")
CHOICE_TYPES = ("single_choice", "multi_choice")
MAX_RATING_STEPS = 10
VALID_LAYOUTS = ("auto", "per_respondent", "single_file")
VALID_THINKING = ("disabled", "adaptive")
VALID_EFFORT = ("low", "medium", "high", "xhigh", "max")


class ConfigError(Exception):
    """設定ファイルの不備。"""


@dataclass(frozen=True)
class Question:
    id: str
    type: str
    text: str
    choices: tuple[str, ...] = ()
    unit: str | None = None
    note: str | None = None
    scale_min: int = 1
    scale_max: int = 5
    scale_labels: tuple[tuple[int, str], ...] = ()

    @property
    def is_choice(self) -> bool:
        return self.type in CHOICE_TYPES

    @property
    def is_rating(self) -> bool:
        return self.type == "rating"

    @property
    def scale_values(self) -> tuple[int, ...]:
        return tuple(range(self.scale_min, self.scale_max + 1))

    def scale_label(self, value: int) -> str:
        """1 → '1：非常に不満足' のような表示名。ラベル未定義なら数字のみ。"""
        label = dict(self.scale_labels).get(value)
        return f"{value}：{label}" if label else str(value)

    @property
    def label(self) -> str:
        """Excel のヘッダ等に使う表示名。"""
        return f"{self.id}: {self.text}"


@dataclass(frozen=True)
class ModelConfig:
    name: str = "claude-sonnet-5"
    max_tokens: int = 16000
    thinking: str = "disabled"
    effort: str | None = None
    temperature: float | None = None
    strict_tools: bool = True
    pdf_max_bytes: int = 20_000_000
    image_dpi: int = 300


@dataclass(frozen=True)
class Meta:
    survey_name: str = "アンケート"
    pages_per_respondent: int = 6
    layout: str = "auto"
    flag_uniform_min_ratings: bool = True


@dataclass(frozen=True)
class Config:
    meta: Meta
    model: ModelConfig
    questions: tuple[Question, ...] = field(default=())

    def question(self, qid: str) -> Question | None:
        return next((q for q in self.questions if q.id == qid), None)


def _as_mapping(value: Any, where: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{where} はマッピング（key: value 形式）で記述してください。")
    return value


def _parse_question(raw: Any, index: int) -> Question:
    where = f"questions[{index}]"
    data = _as_mapping(raw, where)

    qid = str(data.get("id", "")).strip()
    if not qid:
        raise ConfigError(f"{where}: id が空です。")
    if qid.startswith("_"):
        raise ConfigError(f"{where}: id をアンダースコアで始めることはできません（内部フィールド用に予約）。")

    qtype = str(data.get("type", "")).strip()
    if qtype not in QUESTION_TYPES:
        raise ConfigError(
            f"{where} ({qid}): type は {'/'.join(QUESTION_TYPES)} のいずれかにしてください（現在: {qtype!r}）。"
        )

    text = str(data.get("text", "")).strip()
    if not text:
        raise ConfigError(f"{where} ({qid}): text（設問文）が空です。")

    raw_choices = data.get("choices") or []
    if not isinstance(raw_choices, list):
        raise ConfigError(f"{where} ({qid}): choices はリストで記述してください。")
    choices = tuple(str(c).strip() for c in raw_choices)

    if qtype in CHOICE_TYPES:
        if len(choices) < 2:
            raise ConfigError(f"{where} ({qid}): {qtype} には選択肢を2つ以上指定してください。")
        if len(set(choices)) != len(choices):
            raise ConfigError(f"{where} ({qid}): choices に重複があります。")
    elif choices:
        raise ConfigError(f"{where} ({qid}): type={qtype} では choices を指定できません。")

    scale_min, scale_max = 1, 5
    scale_labels: tuple[tuple[int, str], ...] = ()
    if qtype == "rating":
        raw_scale = data.get("scale", [1, 5])
        if not isinstance(raw_scale, list) or len(raw_scale) != 2:
            raise ConfigError(f"{where} ({qid}): scale は [最小, 最大] の形式で指定してください。")
        scale_min, scale_max = int(raw_scale[0]), int(raw_scale[1])
        if scale_min >= scale_max:
            raise ConfigError(f"{where} ({qid}): scale は 最小 < 最大 にしてください。")
        if scale_max - scale_min + 1 > MAX_RATING_STEPS:
            raise ConfigError(f"{where} ({qid}): scale の段階数は {MAX_RATING_STEPS} 以下にしてください。")
        raw_labels = _as_mapping(data.get("labels"), f"{where}.labels")
        labels: list[tuple[int, str]] = []
        for key, value in raw_labels.items():
            try:
                number = int(key)
            except (TypeError, ValueError):
                raise ConfigError(f"{where} ({qid}): labels のキーは数字にしてください（{key!r}）。") from None
            if not scale_min <= number <= scale_max:
                raise ConfigError(f"{where} ({qid}): labels のキー {number} が scale の範囲外です。")
            labels.append((number, str(value).strip()))
        scale_labels = tuple(sorted(labels))
    elif "scale" in data or "labels" in data:
        raise ConfigError(f"{where} ({qid}): scale / labels は type=rating でのみ指定できます。")

    unit = data.get("unit")
    note = data.get("note")
    return Question(
        id=qid,
        type=qtype,
        text=text,
        choices=choices,
        unit=str(unit).strip() if unit else None,
        note=str(note).strip() if note else None,
        scale_min=scale_min,
        scale_max=scale_max,
        scale_labels=scale_labels,
    )


def _parse_model(raw: Any) -> ModelConfig:
    data = _as_mapping(raw, "model")
    defaults = ModelConfig()

    thinking = str(data.get("thinking", defaults.thinking)).strip()
    if thinking not in VALID_THINKING:
        raise ConfigError(f"model.thinking は {'/'.join(VALID_THINKING)} のいずれかにしてください。")

    effort = data.get("effort", defaults.effort)
    if effort is not None:
        effort = str(effort).strip()
        if effort not in VALID_EFFORT:
            raise ConfigError(f"model.effort は {'/'.join(VALID_EFFORT)} または null にしてください。")

    temperature = data.get("temperature", defaults.temperature)
    if temperature is not None:
        temperature = float(temperature)

    return ModelConfig(
        name=str(data.get("name", defaults.name)).strip(),
        max_tokens=int(data.get("max_tokens", defaults.max_tokens)),
        thinking=thinking,
        effort=effort,
        temperature=temperature,
        strict_tools=bool(data.get("strict_tools", defaults.strict_tools)),
        pdf_max_bytes=int(data.get("pdf_max_bytes", defaults.pdf_max_bytes)),
        image_dpi=int(data.get("image_dpi", defaults.image_dpi)),
    )


def _parse_meta(raw: Any) -> Meta:
    data = _as_mapping(raw, "meta")
    defaults = Meta()

    layout = str(data.get("layout", defaults.layout)).strip()
    if layout not in VALID_LAYOUTS:
        raise ConfigError(f"meta.layout は {'/'.join(VALID_LAYOUTS)} のいずれかにしてください。")

    pages = int(data.get("pages_per_respondent", defaults.pages_per_respondent))
    if pages < 1:
        raise ConfigError("meta.pages_per_respondent は 1 以上にしてください。")

    return Meta(
        survey_name=str(data.get("survey_name", defaults.survey_name)).strip(),
        pages_per_respondent=pages,
        layout=layout,
        flag_uniform_min_ratings=bool(
            data.get("flag_uniform_min_ratings", defaults.flag_uniform_min_ratings)
        ),
    )


def load_config(path: str | Path) -> Config:
    """questions.yaml を読み込んで検証済みの Config を返す。"""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"設問定義ファイルが見つかりません: {path}")

    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    data = _as_mapping(raw, str(path))
    raw_questions = data.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise ConfigError(f"{path}: questions が空です。設問を1件以上定義してください。")

    questions = tuple(_parse_question(q, i) for i, q in enumerate(raw_questions))

    seen: set[str] = set()
    for q in questions:
        if q.id in seen:
            raise ConfigError(f"設問IDが重複しています: {q.id}")
        seen.add(q.id)

    return Config(
        meta=_parse_meta(data.get("meta")),
        model=_parse_model(data.get("model")),
        questions=questions,
    )
