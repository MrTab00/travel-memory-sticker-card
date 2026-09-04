"""コマンドラインエントリポイント。"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import env, markers, pricing
from .aggregate import write_excel
from .config import Config, ConfigError, load_config
from .extractor import ExtractionError, Extractor, load_records, save_record
from .markers import MarkerError
from .pdf_utils import (
    PdfError,
    Respondent,
    discover_respondents,
    list_input_pdfs,
    read_page_count,
)
from .schema import build_tool
from .selftest import build_dummy_records

DEFAULT_QUESTIONS = "questions.yaml"
DEFAULT_INPUT = "input"
DEFAULT_OUTPUT = "output"


def _log(message: str = "") -> None:
    print(message, flush=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="survey-pdf-extractor",
        description="スキャンしたアンケートPDFから回答を抽出し、Excel に集計する。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
例:
  python main.py                          # input/ を処理して output/ に Excel を出力
  python main.py --dry-run                # API を呼ばずに概算コストだけ確認
  python main.py --only 回答者A           # 1名だけ処理（サンプル確認用）
  python main.py --aggregate-only         # 既存の output/raw/*.json だけで集計し直す
  python main.py --self-test              # ダミーデータで Excel 生成を確認（API 不要）
""",
    )
    parser.add_argument("--questions", default=DEFAULT_QUESTIONS, help="設問定義ファイル (既定: questions.yaml)")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="入力 PDF のディレクトリ (既定: input)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="出力ディレクトリ (既定: output)")
    parser.add_argument("--layout", choices=["auto", "per_respondent", "single_file", "by_page_marker"], help="入力 PDF の構成（questions.yaml を上書き）")
    parser.add_argument("--show-groups", action="store_true", help="回答者のグループ分けだけ表示して終了する（by_page_marker の確認用）")
    parser.add_argument("--redetect-markers", action="store_true", help="右上の手書き番号を読み取り直す（キャッシュを無視）")
    parser.add_argument("--pages", type=int, help="1名あたりのページ数（questions.yaml を上書き）")
    parser.add_argument("--model", help="使用するモデル（questions.yaml を上書き）")
    parser.add_argument("--only", nargs="+", metavar="ID", help="指定した回答者IDだけ処理する")
    parser.add_argument("--force", action="store_true", help="raw/ に JSON があっても再抽出する")
    parser.add_argument("--images", action="store_true", help="PDF 直送をやめ、最初から画像として送る")
    parser.add_argument("--aggregate-only", action="store_true", help="API を呼ばず、既存の raw/*.json から集計だけ行う")
    parser.add_argument("--dry-run", action="store_true", help="トークン数を数えて概算コストを表示し、抽出はしない")
    parser.add_argument("--self-test", action="store_true", help="ダミーデータでパイプラインを検証する（API 不要）")
    return parser


def _apply_overrides(config: Config, args: argparse.Namespace) -> Config:
    meta = config.meta
    model = config.model
    if args.layout:
        meta = dataclasses.replace(meta, layout=args.layout)
    if args.pages:
        meta = dataclasses.replace(meta, pages_per_respondent=args.pages)
    if args.model:
        model = dataclasses.replace(model, name=args.model)
    return dataclasses.replace(config, meta=meta, model=model)


def _output_path(output_dir: Path, suffix: str = "") -> Path:
    stamp = datetime.now().strftime("%Y%m%d")
    return output_dir / f"aggregate_{stamp}{suffix}.xlsx"


def _report_excel(config: Config, records: list[dict[str, Any]], path: Path) -> None:
    if not records:
        _log("集計対象の抽出結果がありません。Excel は生成しませんでした。")
        return
    if path.exists():
        _log(f"※ 既存の {path.name} を上書きします。")
    path, counts = write_excel(config, records, path)
    _log("")
    _log(f"Excel を生成しました: {path}")
    _log(f"  生データ : {counts['respondents']} 名")
    _log(f"  集計     : {counts['summary_rows']} 行")
    _log(f"  要確認   : {counts['review_rows']} セル（confidence が low / medium のもの）")
    if counts["review_rows"]:
        _log("  → 手書きの誤読は統計に紛れると発見できません。必ず「要確認」シートを原本と突き合わせてください。")


def _check_api_key() -> None:
    if os.environ.get(env.KEY_NAME):
        return
    path = env.env_file_path()
    _log(f"APIキーが設定されていません（{env.KEY_NAME}）。")
    if path.is_file():
        _log(f"  {path} を開き、次の行にキーを書いてください:")
        _log(f"      {env.KEY_NAME}=sk-ant-...")
    else:
        _log("  次のコマンドで設定ファイルを作り、キーを書いてください:")
        _log("      cp .env.example .env && chmod 600 .env && open -e .env")
    raise SystemExit(2)


def _select(respondents: list[Respondent], only: list[str] | None) -> list[Respondent]:
    if not only:
        return respondents
    wanted = set(only)
    selected = [r for r in respondents if r.id in wanted]
    missing = wanted - {r.id for r in selected}
    if missing:
        _log(f"※ 指定された回答者IDが見つかりません: {', '.join(sorted(missing))}")
    return selected


def _discover_by_marker(
    config: Config, input_dir: Path, output_dir: Path, args: argparse.Namespace
) -> tuple[list[Respondent], list[str]]:
    """用紙右上の手書き番号でページを回答者ごとにまとめる。"""
    import anthropic

    pdfs = list_input_pdfs(input_dir)
    if not pdfs:
        raise PdfError(f"{input_dir} に PDF が見つかりません。")

    warnings: list[str] = []
    respondents: list[Respondent] = []
    client = None

    for path in pdfs:
        total = read_page_count(path)
        cache = markers.cache_path(output_dir, path)
        data = None if args.redetect_markers else markers.load_cache(cache)

        if data is None:
            _check_api_key()
            if client is None:
                client = anthropic.Anthropic(max_retries=0)
            _log(f"{path.name}（全{total}ページ）の右上の番号を読み取ります…")
            data = markers.MarkerDetector(config, client, _log).detect(path, total)
            markers.save_cache(cache, data)
            _log(f"  読み取り結果を {cache} に保存しました（次回はこれを再利用します）")
        else:
            warnings.append(f"{path.name}: 既存の番号読み取り結果を使用（{cache.name}）")

        groups, group_warnings = markers.group_by_marker(data, config.meta.pages_per_respondent)
        warnings.extend(f"{path.name}: {w}" for w in group_warnings)

        prefix = config.meta.respondent_id_prefix
        if len(pdfs) > 1:
            prefix = f"{path.stem}_{prefix}"
        respondents.extend(markers.respondents_from_groups(path, groups, prefix))

    seen: set[str] = set()
    for r in respondents:
        if r.id in seen:
            raise MarkerError(f"回答者IDが重複しています: {r.id}")
        seen.add(r.id)

    return respondents, warnings


def _show_groups(respondents: list[Respondent]) -> None:
    _log("")
    _log("回答者ID        ページ                 ページ数  右上の番号")
    _log("-" * 60)
    for r in respondents:
        flag = "" if r.page_count else "  ← ページなし"
        _log(f"{r.id:<14} {r.page_label:<20} {r.page_count:>6}    {r.marker or '-'}{flag}")
    _log("-" * 60)
    _log(f"合計 {len(respondents)} 名 / {sum(r.page_count for r in respondents)} ページ")
    _log("")
    _log("このグループ分けで問題なければ、--show-groups を外して実行してください。")


# ---------------------------------------------------------------------------
# 各モード
# ---------------------------------------------------------------------------
def _run_self_test(config: Config, output_dir: Path) -> int:
    _log("=== セルフテスト（API は呼び出しません）===")
    tool = build_tool(config, strict=config.model.strict_tools)
    fields = len(tool["input_schema"]["properties"])
    _log(f"ツールスキーマを生成: {tool['name']} / フィールド {fields} 件")

    records = build_dummy_records(config)
    raw_dir = output_dir / "selftest" / "raw"
    for record in records:
        save_record(record, raw_dir)
    _log(f"ダミーの抽出結果 {len(records)} 件を {raw_dir} に保存しました。")

    _report_excel(config, records, _output_path(output_dir / "selftest", "_selftest"))
    _log("")
    _log("セルフテスト完了。実データを input/ に置いて `python main.py` を実行してください。")
    return 0


def _run_dry_run(config: Config, respondents: list[Respondent]) -> int:
    _check_api_key()
    extractor = Extractor(config, force_images=False, log=_log)
    sample = respondents[0]
    _log(f"サンプル: {sample.id}（{sample.page_count}ページ）のトークン数を数えます…")
    try:
        input_tokens = extractor.count_tokens(sample)
    except Exception as exc:  # noqa: BLE001 - 見積りは失敗しても致命的でない
        _log(f"トークン数の取得に失敗しました: {exc}")
        return 1

    estimated_output = 250 * len(config.questions) + 500  # 1設問あたりの目安
    per_person = pricing.estimate_usd(config.model.name, input_tokens, estimated_output)
    _log("")
    _log(f"1名あたり: 入力 {input_tokens:,} tok / 出力(推定) {estimated_output:,} tok")
    if per_person is not None:
        total = per_person * len(respondents)
        _log(f"1名あたり概算: ${per_person:.3f}（約 {per_person * pricing.DEFAULT_USD_JPY:,.0f} 円）")
        _log(
            f"全{len(respondents)}名の概算: ${total:.2f}"
            f"（約 {total * pricing.DEFAULT_USD_JPY:,.0f} 円 / 1USD={pricing.DEFAULT_USD_JPY:.0f}円換算）"
        )
    _log("※ 出力トークン数は設問数からの推定値です。実際の請求額は公式の料金表と使用実績を確認してください。")
    return 0


def _run_extraction(
    config: Config, respondents: list[Respondent], output_dir: Path, args: argparse.Namespace
) -> int:
    _check_api_key()
    raw_dir = output_dir / "raw"
    extractor = Extractor(config, force_images=args.images, log=_log)

    succeeded: list[dict[str, Any]] = []
    skipped: list[str] = []
    failures: list[tuple[str, str]] = []
    totals = {"input": 0, "output": 0}

    total = len(respondents)
    for index, respondent in enumerate(respondents, start=1):
        existing = raw_dir / f"{respondent.id}.json"
        if existing.exists() and not args.force:
            _log(f"[{index}/{total}] スキップ（抽出済み）: {respondent.id}")
            skipped.append(respondent.id)
            continue

        _log(f"[{index}/{total}] 処理中: {respondent.id} ...")
        try:
            record = extractor.extract(respondent)
        except ExtractionError as exc:
            _log(f"    失敗: {exc}")
            failures.append((respondent.id, str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 - 1名の失敗で全体を止めない
            _log(f"    想定外のエラー: {type(exc).__name__}: {exc}")
            failures.append((respondent.id, f"{type(exc).__name__}: {exc}"))
            continue

        save_record(record, raw_dir)
        succeeded.append(record)
        totals["input"] += record["usage"].get("input_tokens", 0)
        totals["output"] += record["usage"].get("output_tokens", 0)

        review = sum(1 for a in record["answers"].values() if a["confidence"] in ("low", "medium"))
        _log(f"    完了（要確認 {review} 件 / 全 {len(record['answers'])} 設問）")

    _log("")
    _log(f"抽出: 成功 {len(succeeded)} 名 / スキップ {len(skipped)} 名 / 失敗 {len(failures)} 名")
    if succeeded:
        _log("今回の API 使用量: " + pricing.format_cost(config.model.name, totals["input"], totals["output"]))

    if failures:
        _log("")
        _log("--- 失敗した回答者 ---")
        for rid, reason in failures:
            _log(f"  {rid}: {reason}")
        _log("（失敗分は raw/ に JSON が無いので、原因を直して再実行すれば続きから処理されます）")

    records = load_records(raw_dir)
    _report_excel(config, records, _output_path(output_dir))
    return 1 if failures else 0


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # .env があればキーを読み込む（環境変数が既にあればそちらを優先）
    if env.load_env_file():
        path = env.env_file_path()
        _log(f"APIキー   : {path.name} から読み込みました（{env.mask(os.environ[env.KEY_NAME])}）")
        if env.is_world_readable(path):
            _log(f"※ {path.name} が他のユーザーからも読める権限です。chmod 600 {path.name} を推奨します。")

    try:
        config = _apply_overrides(load_config(args.questions), args)
    except ConfigError as exc:
        _log(f"設定エラー: {exc}")
        return 2

    output_dir = Path(args.output)
    _log(f"アンケート : {config.meta.survey_name}")
    _log(f"設問数     : {len(config.questions)}")
    _log(f"モデル     : {config.model.name}")
    _log("")

    if args.self_test:
        return _run_self_test(config, output_dir)

    if args.aggregate_only:
        records = load_records(output_dir / "raw")
        _log(f"{output_dir / 'raw'} から {len(records)} 件の抽出結果を読み込みました。")
        _report_excel(config, records, _output_path(output_dir))
        return 0

    try:
        if config.meta.layout == "by_page_marker":
            respondents, warnings = _discover_by_marker(config, Path(args.input), output_dir, args)
        else:
            respondents, warnings = discover_respondents(
                Path(args.input), config.meta.layout, config.meta.pages_per_respondent
            )
    except (PdfError, MarkerError) as exc:
        _log(f"入力エラー: {exc}")
        return 2

    for warning in warnings:
        _log(f"※ {warning}")

    if args.show_groups:
        _show_groups(respondents)
        return 0
    respondents = _select(respondents, args.only)
    if not respondents:
        _log("処理対象の回答者がいません。")
        return 2
    _log(f"回答者 {len(respondents)} 名分を検出しました。")
    _log("")

    if args.dry_run:
        return _run_dry_run(config, respondents)

    return _run_extraction(config, respondents, output_dir, args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
