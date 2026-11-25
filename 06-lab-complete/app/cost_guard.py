"""
Cost guard (tiêu chí 8).

  • Cộng dồn chi phí LLM (tính theo token) vào Redis, theo ngày và theo tháng (UTC)
  • Tổng chi phí đã đạt DAILY_BUDGET_USD hoặc MONTHLY_BUDGET_USD → HTTP 402
  • Key theo ngày/tháng (cost:daily:2026-11-25, cost:monthly:2026-11) nên tự "reset" khi sang
    ngày/tháng mới; key cũ tự xoá nhờ EXPIRE
  • Lưu trong Redis nên mọi instance chia chung một ngân sách và restart không làm mất số đã tiêu
"""
from datetime import datetime, timezone

from fastapi import HTTPException

from app.config import settings
from app.redis_client import KEY_PREFIX, r

# Giá USD / 1K tokens (tham khảo gpt-4o-mini)
PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006

DAILY_TTL = 2 * 24 * 3600
MONTHLY_TTL = 32 * 24 * 3600


def _keys() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return (f"{KEY_PREFIX}cost:daily:{now:%Y-%m-%d}",
            f"{KEY_PREFIX}cost:monthly:{now:%Y-%m}")


def estimate_tokens(text: str) -> int:
    """Ước lượng thô ~4 ký tự / token (mock LLM không trả usage thật)."""
    return max(1, len(text) // 4)


def current_spend() -> tuple[float, float]:
    daily_key, monthly_key = _keys()
    daily, monthly = r.mget(daily_key, monthly_key)
    return float(daily or 0), float(monthly or 0)


def check_budget() -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget → raise HTTPException(status_code=402)."""
    daily, monthly = current_spend()
    if daily >= settings.daily_budget_usd or monthly >= settings.monthly_budget_usd:
        period = "daily" if daily >= settings.daily_budget_usd else "monthly"
        raise HTTPException(
            status_code=402,
            detail={
                "error": f"{period.capitalize()} budget exceeded",
                "daily_spent_usd": round(daily, 6),
                "daily_budget_usd": settings.daily_budget_usd,
                "monthly_spent_usd": round(monthly, 6),
                "monthly_budget_usd": settings.monthly_budget_usd,
            },
        )


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí của request vào tổng, trả về chi phí request."""
    cost = (input_tokens / 1000) * PRICE_INPUT_PER_1K + (output_tokens / 1000) * PRICE_OUTPUT_PER_1K
    daily_key, monthly_key = _keys()
    pipe = r.pipeline(transaction=True)
    pipe.incrbyfloat(daily_key, cost)
    pipe.expire(daily_key, DAILY_TTL)
    pipe.incrbyfloat(monthly_key, cost)
    pipe.expire(monthly_key, MONTHLY_TTL)
    pipe.execute()
    return cost
