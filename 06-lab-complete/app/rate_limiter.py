"""
Rate limiting (tiêu chí 7).

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây (sliding window log)
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State lưu trong Redis (sorted set, score = thời điểm request) nên mọi worker / instance
    dùng chung và vẫn còn sau khi restart agent

Cả chuỗi ZREMRANGEBYSCORE → ZCARD → ZADD → PEXPIRE chạy trong một Lua script, tức là atomic:
hai worker nhận request cùng lúc không thể cùng thấy "còn 1 chỗ" rồi cùng được cho qua.
"""
import math
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import KEY_PREFIX, r

WINDOW_MS = 60_000

_SLIDING_WINDOW = r.register_script(
    """
    local key    = KEYS[1]
    local now    = tonumber(ARGV[1])
    local window = tonumber(ARGV[2])
    local limit  = tonumber(ARGV[3])

    redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
    local count = redis.call('ZCARD', key)
    if count >= limit then
        local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
        return {0, count, tonumber(oldest[2])}
    end
    redis.call('ZADD', key, now, ARGV[4])
    redis.call('PEXPIRE', key, window)
    return {1, count + 1, 0}
    """
)


def check_rate_limit(key: str) -> dict:
    """key: định danh client (hash của API key). Trả về thông tin limit, hoặc raise 429."""
    limit = settings.rate_limit_per_minute
    now_ms = int(time.time() * 1000)
    allowed, count, oldest_ms = _SLIDING_WINDOW(
        keys=[f"{KEY_PREFIX}ratelimit:{key}"],
        args=[now_ms, WINDOW_MS, limit, f"{now_ms}-{uuid.uuid4().hex[:8]}"],
    )
    if not allowed:
        retry_after = max(1, math.ceil((int(oldest_ms) + WINDOW_MS - now_ms) / 1000))
        raise HTTPException(
            status_code=429,
            detail={"error": "Rate limit exceeded", "limit": limit, "window_seconds": 60,
                    "retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after),
                     "X-RateLimit-Limit": str(limit),
                     "X-RateLimit-Remaining": "0"},
        )
    return {"limit": limit, "remaining": max(0, limit - int(count))}
