# Deployment Information

## Public URL

https://day14-agent-l0rz.onrender.com

(`l` thường + số `0` + `rz`)

## Platform

**Render**: Blueprint [`06-lab-complete/render.yaml`](06-lab-complete/render.yaml), gói Free.

| Tài nguyên | Loại | Ghi chú |
|---|---|---|
| `day14-agent` | Web Service, runtime Docker, region Singapore | Build từ [`06-lab-complete/Dockerfile`](06-lab-complete/Dockerfile) (multi-stage, non-root), health check `GET /health`, auto-deploy khi push `main` |
| `day14-redis` | Key Value (Redis-compatible), region Singapore | `ipAllowList: []`: chỉ nhận kết nối nội bộ từ service trên Render; lưu state rate limit + cost guard |

Deploy: push repo lên GitHub → Render Dashboard → **New → Blueprint** → chọn repo, nhánh `main`, Blueprint Path `06-lab-complete/render.yaml` → **Deploy Blueprint**. Commit đầu tiên được deploy: `86e0a8e` (build + deploy 46,7 s).

Railway (Exercise 3.1) đã thử trước: cài Railway CLI, `railway login` thành công, nhưng `railway init` trả về *"Your trial has expired. Please select a plan to continue using Railway."*. Tài khoản hết hạn dùng thử nên không tạo được project nếu không nâng cấp gói trả phí, vì vậy chuyển sang Render (gói Free). `06-lab-complete/railway.toml` vẫn được giữ đúng định dạng để deploy được khi có gói Railway.

## Test Commands

```bash
BASE=https://day14-agent-l0rz.onrender.com
export AGENT_API_KEY='<giá trị AGENT_API_KEY trong Render → day14-agent → Environment>'
```

### Health check

```bash
curl $BASE/health
# {"status":"ok","version":"1.0.0","environment":"production","uptime_seconds":137.7}

curl $BASE/ready
# {"ready":true}          ← agent kết nối được Redis (day14-redis)
```

### API test (with authentication)

```bash
# Không có key → 401
curl -X POST $BASE/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
# {"detail":"Invalid or missing API key. Include header: X-API-Key: <key>"}

# Có key → 200
curl -X POST $BASE/ask \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "Hello"}'
```

### Tự kiểm CP6 (chỉ in mã HTTP, không lộ key)

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả (chạy 2026-11-25, ảnh [`screenshots/test.png`](screenshots/test.png)):

```text
200
200
401
200
```

Log JSON tương ứng trên Render (ảnh [`screenshots/running.png`](screenshots/running.png)). Request không key bị chặn trước khi gọi LLM; request có key ghi `llm_call` với định danh client là hash của key, không phải key gốc:

```json
{"ts": "2026-11-25T13:55:26.318+00:00", "lvl": "INFO", "logger": "agent", "event": "request", "method": "POST", "path": "/ask", "status": 401, "ms": 1.2}
{"ts": "2026-11-25T13:55:27.074+00:00", "lvl": "INFO", "logger": "agent", "event": "llm_call", "client": "b645a8299854ae7c", "user_id": null, "input_tokens": 1, "output_tokens": 24, "cost_usd": 1.455e-05}
{"ts": "2026-11-25T13:55:27.074+00:00", "lvl": "INFO", "logger": "agent", "event": "request", "method": "POST", "path": "/ask", "status": 200, "ms": 119.3}
```

Kiểm thêm: `GET $BASE/docs` → `404` (Swagger UI bị tắt khi `ENVIRONMENT=production`), và script chấm:

```text
$ python check_production_ready.py --static-only --public-url https://day14-agent-l0rz.onrender.com
     ✅ Public URL: GET https://day14-agent-l0rz.onrender.com/health → 200
```

> Gói Free của Render cho instance "ngủ" sau 15 phút không có request; request đầu tiên sau đó mất ~50 giây để khởi động lại (lần chạy script đầu tiên bị timeout 15 s vì lý do này, lần sau khi instance đã thức thì trả 200 trong 0,17 s).

## Environment Variables Set

Tất cả đặt trên Render (khai báo trong `render.yaml`), **không có giá trị secret nào trong code hay trong repo**:

| Biến | Giá trị / nguồn |
|---|---|
| `AGENT_API_KEY` | `generateValue: true`: Render tự sinh chuỗi ngẫu nhiên, chỉ xem được trong Dashboard |
| `REDIS_URL` | `fromService` → `day14-redis` `connectionString` (URL nội bộ của Key Value) |
| `ENVIRONMENT` | `production` (bắt buộc có `AGENT_API_KEY`, tắt `/docs`) |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `MONTHLY_BUDGET_USD` | `10.0` |
| `PORT` | Render tự cấp (mặc định 10000); `CMD` trong Dockerfile dùng `${PORT:-8000}` |
| `LOG_LEVEL` | không đặt → mặc định `INFO` trong `app/config.py` |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png): service `day14-agent` trạng thái **Live**, Docker, Free, Blueprint managed, commit `86e0a8e`
- [Service running](screenshots/running.png): log JSON của agent trên Render
- [Test results](screenshots/test.png): 4 lệnh tự kiểm: `200 200 401 200`
