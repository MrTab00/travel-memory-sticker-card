"""抽出用プロンプト。"""

from __future__ import annotations

from .config import Config, Question
from .schema import TOOL_NAME

SYSTEM_PROMPT = """\
あなたは、日本語の紙アンケート（顧客が手書きで記入し、スキャンした画像）を読み取る
きわめて慎重な事務処理の専門家です。読み取り結果はそのまま統計に使われるため、
誤読が1件混ざるだけで結論が変わります。正確さを最優先してください。

【絶対に守るルール】
1. 推測で埋めない。読めないものは「読めない」と返す。
   - 空欄（何も書かれていない）… status="blank", value=null, raw_text=null, confidence="high"
   - 記入はあるが判読できない … status="unreadable", value=null,
     raw_text には読めた範囲の文字（例: "○○の□□化"）を入れ、confidence="low"
   - 未記入と判読不能を必ず区別すること。空文字列は使わず null を使うこと。
2. 選択式のチェックを見落とさない。チェックの表現は多様である。
   レ点 / ✓ / × / 丸囲み / 塗りつぶし / 下線 / 選択肢を囲む枠 / 選択肢の脇の書き込み。
   薄い鉛筆書きや、印刷された枠からはみ出したマークも見逃さないこと。
3. 修正跡（二重線・×で消して書き直し・修正液）がある場合は、最終的な回答を採用する。
   打ち消された側は採用せず、その事実を raw_text に書き添える
   （例: "検討中を二重線で消し、着手済に変更"）。
4. 選択式の value は、与えられた選択肢の文字列と完全に一致させる（表記を変えない）。
   どの選択肢とも一致しない書き込み（独自記入・「その他（〜）」の中身など）は、
   value を最も近い選択肢にせず、raw_text に原文を入れて confidence="low" とする。
5. 自由記述は要約・言い換え・誤字修正をしない。改行も含め、書かれたままを raw_text に入れる。
   判読できない文字は "□" で埋め、その設問の confidence を下げる。
6. 数値は value に半角数字（数値型）で入れ、raw_text には原文ママ
   （例: "約５０台" → value=50, raw_text="約５０台", confidence="medium"）。
7. confidence は自分の読みへの自信であり、回答者の書きぶりの丁寧さではない。
   少しでも迷ったら medium、推測が入るなら low。high は「明確に読める」場合のみ。
8. 5段階評価（rating）は、**丸（○）で囲まれている数字そのもの**を返す。
   選択肢の文言や、他の設問・自由記述の内容から「この人はきっと満足しているはず」と
   推測して数字を変えてはならない。丸が2つの数字にまたがっている、または
   どこに付いているか特定できない場合は null + confidence="low" とし、
   raw_text にその状況を書く（例: "1と2の間に丸"）。
9. page には、その設問が印刷されていたページ番号（この回答者の PDF 内で1始まり）を入れる。

【出力方法】
必ず {tool_name} ツールを1回だけ呼び出して結果を返すこと。
ツール呼び出し以外の説明文・前置き・要約は一切出力しないこと。
""".format(tool_name=TOOL_NAME)


def _question_line(q: Question) -> str:
    parts = [f"- {q.id} [{q.type}] {q.text}"]
    if q.choices:
        parts.append(f"    選択肢: {' / '.join(q.choices)}")
    if q.is_rating:
        parts.append(f"    尺度（丸が付いた数字を返す）: {' / '.join(q.scale_label(v) for v in q.scale_values)}")
    if q.unit:
        parts.append(f"    単位: {q.unit}")
    if q.note:
        parts.append(f"    注意: {q.note}")
    return "\n".join(parts)


def build_user_instruction(config: Config, page_count: int) -> str:
    """PDF / 画像に続けて渡す指示文。"""
    questions = "\n".join(_question_line(q) for q in config.questions)
    return f"""\
添付は「{config.meta.survey_name}」に回答者1名が記入した用紙のスキャン（全{page_count}ページ）です。
以下の全{len(config.questions)}設問について、記入内容を読み取ってください。

{questions}

すべてのページに目を通し、設問の並び順ではなく設問文そのものを手がかりに対応付けてください。
用紙の隅の書き込み・欄外のコメントも、該当する設問の raw_text に含めてください。
読み取れた内容を {TOOL_NAME} ツールで返してください。"""
