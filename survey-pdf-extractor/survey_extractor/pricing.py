"""概算コスト計算用の料金表（1M トークンあたりの USD）。

料金は変わりうるので、正確な値は公式の料金ページで確認すること。
"""

from __future__ import annotations

USD_PER_MTOK: dict[str, tuple[float, float]] = {
    # model prefix: (input, output)
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

DEFAULT_USD_JPY = 155.0


def rates(model: str) -> tuple[float, float] | None:
    for prefix, value in USD_PER_MTOK.items():
        if model.startswith(prefix):
            return value
    return None


def estimate_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    found = rates(model)
    if found is None:
        return None
    price_in, price_out = found
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def format_cost(model: str, input_tokens: int, output_tokens: int) -> str:
    usd = estimate_usd(model, input_tokens, output_tokens)
    if usd is None:
        return f"入力 {input_tokens:,} tok / 出力 {output_tokens:,} tok（{model} の料金表が未登録のため金額は省略）"
    return (
        f"入力 {input_tokens:,} tok / 出力 {output_tokens:,} tok "
        f"≒ ${usd:.2f}（約 {usd * DEFAULT_USD_JPY:,.0f} 円 / 1USD={DEFAULT_USD_JPY:.0f}円換算）"
    )
