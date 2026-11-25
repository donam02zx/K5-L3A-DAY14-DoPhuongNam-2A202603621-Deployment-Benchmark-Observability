"""
API Key authentication (tiêu chí 6).

  • Client gửi header  X-API-Key: <key>
  • Key phải trùng biến môi trường AGENT_API_KEY
  • Thiếu hoặc sai key → HTTP 401
"""
import hashlib
import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """Dependency cho /ask. Trả về API key hợp lệ để dùng làm định danh rate limit."""
    expected = settings.agent_api_key
    # compare_digest: thời gian so sánh không phụ thuộc vào số ký tự đúng (chống timing attack).
    # Chưa cấu hình key (expected rỗng) → từ chối tất cả, không bao giờ "mở cửa" mặc định.
    if not api_key or not expected or not hmac.compare_digest(api_key.encode(), expected.encode()):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key. Include header: X-API-Key: <key>",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return api_key


def key_id(api_key: str) -> str:
    """Định danh ngắn của key để làm khoá Redis / log — không lưu key gốc."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]
