"""
Exercise 4.4 — Cost guard lưu trong Redis.

Khác với cost_guard.py (in-memory, mất khi restart, mỗi instance đếm riêng),
bản này lưu chi tiêu trong Redis nên:
  - nhiều instance agent dùng chung một con số,
  - restart agent không reset budget,
  - tự reset đầu tháng nhờ key theo tháng + TTL.

Chạy self-test (cần Redis):
    docker run -d --name k5-day14-redis -p 6379:6379 redis:7-alpine
    REDIS_URL=redis://localhost:6379/0 python cost_guard_redis.py
"""
import os
from datetime import datetime, timezone

import redis

MONTHLY_BUDGET_USD = float(os.getenv("MONTHLY_BUDGET_USD", "10.0"))
KEY_TTL_SECONDS = 32 * 24 * 3600  # dài hơn 1 tháng một chút, tháng mới dùng key mới

r = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)

# Kiểm tra và cộng chi phí trong MỘT lệnh atomic trên Redis.
# Nếu làm GET rồi INCRBYFLOAT ở Python (như lời giải mẫu), hai request đồng thời
# có thể cùng đọc "còn budget" rồi cùng cộng → vượt budget. Lua script chạy
# nguyên khối trên Redis nên không có race condition này.
_CHECK_AND_SPEND = r.register_script(
    """
    local current = tonumber(redis.call('GET', KEYS[1]) or '0')
    local cost = tonumber(ARGV[1])
    local budget = tonumber(ARGV[2])
    if current + cost > budget then
        return 0
    end
    redis.call('INCRBYFLOAT', KEYS[1], cost)
    redis.call('EXPIRE', KEYS[1], ARGV[3])
    return 1
    """
)


def _month_key(user_id: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"budget:{user_id}:{now:%Y-%m}"


def check_budget(user_id: str, estimated_cost: float) -> bool:
    """
    Return True nếu còn budget (và đã trừ estimated_cost), False nếu vượt.

    - Mỗi user có budget MONTHLY_BUDGET_USD (mặc định $10) mỗi tháng.
    - Chi tiêu lưu ở key budget:<user>:<YYYY-MM> trong Redis.
    - Sang tháng mới → key mới bắt đầu từ 0; key cũ tự hết hạn sau 32 ngày.
    """
    if estimated_cost < 0:
        raise ValueError("estimated_cost must be >= 0")
    allowed = _CHECK_AND_SPEND(
        keys=[_month_key(user_id)],
        args=[estimated_cost, MONTHLY_BUDGET_USD, KEY_TTL_SECONDS],
    )
    return bool(allowed)


def spent(user_id: str) -> float:
    return float(r.get(_month_key(user_id)) or 0)


if __name__ == "__main__":
    user = "selftest-user"
    r.delete(_month_key(user))
    print(f"budget/tháng = ${MONTHLY_BUDGET_USD}, key = {_month_key(user)}")
    results = [check_budget(user, 3.0) for _ in range(4)]
    print(f"4 request x $3.00 -> {results}   (đã tiêu ${spent(user):.2f})")
    print(f"thêm $1.00 -> {check_budget(user, 1.0)}   (đã tiêu ${spent(user):.2f})")
    print(f"thêm $0.01 -> {check_budget(user, 0.01)}   (đã tiêu ${spent(user):.2f})")
    print(f"TTL của key = {r.ttl(_month_key(user))} giây (~{r.ttl(_month_key(user)) / 86400:.0f} ngày)")
    r.delete(_month_key(user))
