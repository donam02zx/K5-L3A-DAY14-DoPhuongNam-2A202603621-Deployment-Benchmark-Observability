"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

  • Đọc REDIS_URL từ config
  • Không ping lúc import: redis-py chỉ mở kết nối ở lệnh đầu tiên, nên app vẫn khởi động
    được (và /health vẫn 200) khi Redis chưa sẵn sàng
  • Timeout ngắn để /ready trả 503 nhanh thay vì treo khi Redis chết
"""
import redis

from app.config import settings

KEY_PREFIX = "day14:"

r = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=2,
    retry_on_timeout=False,
)


def redis_healthy() -> bool:
    try:
        return bool(r.ping())
    except redis.RedisError:
        return False
