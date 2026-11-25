# Day 14 Lab - Mission Answers

> Môi trường chạy: Windows 11, Python 3.14.6 (venv `.venv`), Docker 29.5.2, Docker Compose v5.1.4.

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found

Đọc `01-localhost-vs-production/develop/app.py`:

1. **Hardcode secret trong code** (dòng 17–18): `OPENAI_API_KEY = "sk-hardcoded-..."` và `DATABASE_URL = "postgresql://admin:password123@..."`. Push lên GitHub là lộ key/mật khẩu DB, và muốn đổi key phải sửa code + deploy lại.
2. **Log ra secret** (dòng 34): `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")`. Ai đọc được log (log aggregator, người vận hành) là có key. Khi chạy mình thấy đúng dòng `[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this` trong log.
3. **Dùng `print()` thay vì logging có cấu trúc** (dòng 33, 38): không có level, không có timestamp, không parse được bằng máy; còn in nguyên câu hỏi/câu trả lời của người dùng.
4. **Config cứng trong code** (dòng 21–22): `DEBUG = True`, `MAX_TOKENS = 500`. Không đổi được theo môi trường dev/staging/prod mà không sửa code.
5. **Không có health check** (dòng 42): `GET /health` trả 404 → platform (Railway/Render/K8s) không biết agent còn sống hay đã treo để restart, load balancer không biết khi nào route traffic.
6. **Bind `host="localhost"`** (dòng 51): trong container chỉ nhận kết nối từ chính container, bên ngoài không gọi vào được. Production phải bind `0.0.0.0`.
7. **Port cứng `port=8000`** (dòng 52): Railway/Render inject cổng qua biến `PORT`, cứng 8000 thì platform không route được vào app.
8. **`reload=True`** (dòng 53): chế độ dev, tốn CPU để theo dõi file, tạo process con; không được bật trong production.
9. **Không xử lý shutdown**: không có lifespan/SIGTERM handler, nên khi platform tắt container thì request đang xử lý bị cắt ngang.
10. **Không validate input**: `question: str` không giới hạn độ dài, không có xử lý lỗi → lỗi nội bộ thành `500 Internal Server Error`.

### Exercise 1.2: Chạy bản develop

```bash
cd 01-localhost-vs-production/develop
pip install -r requirements.txt
python app.py
```

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
content-type: application/json

{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 422 Unprocessable Content
{"detail":[{"type":"missing","loc":["query","question"],"msg":"Field required","input":null}]}

$ curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/health
404
```

Log server (lộ secret):

```text
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.
```

**Quan sát thêm: một ví dụ "it works on my machine" có thật.** Lần chạy đầu trên Windows, cùng request đó trả `500 Internal Server Error`:

```text
File ".../develop/app.py", line 38, in ask_agent
    print(f"[DEBUG] Response: {response}")
UnicodeEncodeError: 'charmap' codec can't encode character 'Đ' in position 18
```

Console Windows dùng encoding cp1252, nên `print()` câu trả lời tiếng Việt làm crash request. Trên máy Linux/macOS (UTF-8) thì không bị. Chạy lại với `PYTHONUTF8=1` thì trả 200. Đây đúng là lý do cần đóng gói môi trường (Docker) và không để logic phụ thuộc vào `print()`.

**Kết luận:** chạy được, nhưng chưa production-ready: lộ secret, không có health check, chỉ nghe localhost, crash vì khác biệt môi trường.

### Exercise 1.3: Comparison table

Chạy bản production (`cp .env.example .env && python app.py`):

```text
$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). ...","model":"gpt-4o-mini"}
$ curl -X POST ... -d '{"question":""}'          -> 422
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":4.4,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T09:08:08.467141+00:00"}
$ curl http://localhost:8000/ready
{"ready":true}
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---------|---------|------------|---------------------|
| Config | Hardcode trong code (`DEBUG=True`, key, DB URL) | Đọc từ env vars qua `config.py` (`Settings` dataclass), có `validate()` fail-fast khi thiếu `AGENT_API_KEY` ở production | Cùng một image chạy được ở dev/staging/prod chỉ bằng đổi env; secret không nằm trong Git; thiếu config quan trọng thì app dừng ngay lúc khởi động thay vì lỗi lúc đang phục vụ |
| Secrets | Key + mật khẩu DB trong code, còn in ra log | Lấy từ env, log chỉ ghi `question_length`, không log secret/nội dung | Repo, log và màn hình người vận hành đều có thể bị lộ; secret chỉ nên nằm ở secret store của platform |
| Health check | Không có (`/health` → 404) | `/health` (liveness: uptime, version) + `/ready` (readiness: 503 khi chưa sẵn sàng) | Platform dùng liveness để restart container bị treo, load balancer dùng readiness để chỉ gửi traffic vào instance đã sẵn sàng |
| Logging | `print()` thường | Python `logging` với level, nội dung là JSON (`{"event":"agent_request",...}`) | Log có cấu trúc thì parse/search/alert được trong Loki/Datadog; có level để tắt DEBUG ở prod |
| Host / Port | `localhost:8000` cứng | `HOST=0.0.0.0`, `PORT` từ env | Container cần nghe trên `0.0.0.0` để nhận traffic từ ngoài; platform cloud tự cấp `PORT` |
| Reload / Debug | `reload=True` luôn bật | `reload` chỉ khi `DEBUG=true` | Reload tốn tài nguyên và có thể nạp code lạ; prod cần process ổn định |
| Shutdown | Đột ngột, không có hook | `lifespan` (startup/shutdown) + handler `SIGTERM`, set `is_ready=False` trước khi tắt | Khi deploy/scale, platform gửi SIGTERM: app ngừng nhận request mới, xử lý xong request đang chạy rồi mới thoát → người dùng không bị lỗi giữa chừng |
| Input validation | Lỗi nội bộ → 500 | Thiếu `question` → 422 có message rõ | Client biết sai ở đâu; không lộ stack trace |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ frontend được phép mới gọi được API từ trình duyệt |

**Nhận xét thêm khi chạy bản production:** log thực tế in ra dạng `INFO:     127.0.0.1 - "POST /ask HTTP/1.1" 200 OK` chứ không thấy dòng JSON `agent_request`. Nguyên nhân: `config.py` gọi `logging.warning(...)` lúc import (thiếu `OPENAI_API_KEY`), khiến root logger được cấu hình mặc định trước, nên `logging.basicConfig(format=JSON, level=INFO)` trong `app.py` không còn tác dụng. Cách sửa: gọi `basicConfig(..., force=True)` hoặc cấu hình logging trước khi import `config`. Bài học: thứ tự khởi tạo logging cũng là một chỗ dễ sai khi đưa lên production.

## Part 2: Docker

### Exercise 2.1: Dockerfile questions (`02-docker/develop/Dockerfile`)

1. **Base image:** `python:3.11`, bản Python đầy đủ (Debian + compiler + nhiều thư viện hệ thống), nên rất nặng.
2. **Working directory:** `/app`. Mọi lệnh `COPY`, `RUN`, `CMD` sau đó chạy tương đối với thư mục này trong container.
3. **Vì sao `COPY requirements.txt` trước `COPY app.py`:** Docker cache theo từng layer, một layer thay đổi thì mọi layer sau nó phải build lại. Code sửa thường xuyên, còn dependencies hiếm khi đổi. Copy `requirements.txt` và `pip install` trước thì khi chỉ sửa `app.py`, layer `pip install` (chậm nhất) được lấy lại từ cache, build chỉ mất vài giây. Nếu `COPY . .` trước thì mỗi lần sửa code là cài lại toàn bộ dependencies.
4. **CMD vs ENTRYPOINT:** `CMD` là lệnh/tham số mặc định, bị ghi đè khi truyền lệnh khác lúc chạy (`docker run my-agent:develop python -c "..."`). `ENTRYPOINT` là executable cố định của container; tham số của `docker run` được nối thêm vào sau nó (muốn đổi phải dùng `--entrypoint`). Thường dùng kết hợp: `ENTRYPOINT ["uvicorn"]` + `CMD ["main:app","--port","8000"]`. Cả hai nên viết dạng exec (JSON array) để process là PID 1 và nhận được SIGTERM trực tiếp.

### Exercise 2.2: Build và run bản develop

```bash
docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .   # chạy từ gốc repo
docker run -d --name k5-day14-develop -p 8000:8000 my-agent:develop
```

```text
$ curl -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
HTTP/1.1 200 OK
server: uvicorn
content-type: application/json

{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}

$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":1.1,"container":true}

$ docker logs k5-day14-develop
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     172.17.0.1:54098 - "POST /ask?question=What%20is%20Docker%3F HTTP/1.1" 200 OK
```

Lưu ý: trong container, câu trả lời tiếng Việt không còn gây lỗi encoding như khi chạy Python trực tiếp trên Windows ở Part 1, vì image Linux dùng UTF-8. Đó chính là lợi ích "môi trường nhất quán" của Docker.

### Exercise 2.3: Multi-stage build và kích thước image

`02-docker/production/Dockerfile`:

- **Stage 1 (`builder`, `python:3.11-slim`):** cài `gcc`, `libpq-dev` (công cụ build) rồi `pip install --user -r requirements.txt` vào `/root/.local`. Stage này chỉ để build, không được deploy.
- **Stage 2 (`runtime`, `python:3.11-slim` mới):** tạo user `appuser` (không chạy root), `COPY --from=builder /root/.local` (chỉ lấy các package đã cài, 37.8 MB theo `docker history`), copy `main.py` + `utils/`, thêm `HEALTHCHECK`, chạy `uvicorn` với 2 worker.
- **Vì sao nhỏ hơn:** (1) base `slim` thay vì bản full; (2) `gcc`, header, apt cache và pip cache chỉ nằm ở stage builder, bị bỏ lại hoàn toàn; image cuối chỉ có Python runtime + site-packages + code. Ngoài ra ít package hơn thì bề mặt tấn công nhỏ hơn và pull/deploy nhanh hơn.

Đo bằng `docker images` / `docker image inspect --format '{{.Size}}'`:

```text
$ docker images my-agent
IMAGE               ID             DISK USAGE
my-agent:advanced   f748a635fd0a        163MB
my-agent:develop    1d54027e526b       1.15GB
```

| Image | Base | Size (bytes) | Size |
|---|---|---:|---:|
| `my-agent:develop` | `python:3.11` single-stage | 1,154,311,927 | **1154.3 MB** |
| `my-agent:advanced` | `python:3.11-slim` multi-stage | 162,882,434 | **162.9 MB** |

- **Chênh lệch:** 1154.3 − 162.9 = 991.4 MB, tức production **nhỏ hơn 85.9%** (≈ 7.1 lần), và dưới mức 500 MB.

### Exercise 2.4: Docker Compose stack

Chạy (cần file `.env.local` rỗng, dùng `-p` để tách project):

```bash
cd 02-docker/production
touch .env.local
docker compose -p k5-day14-docker up -d --build
```

**Các service được start:**

```text
$ docker compose -p k5-day14-docker ps
agent   k5-day14-docker-agent   Up 7 seconds (healthy)    8000/tcp
nginx   nginx:alpine            Up 7 seconds              0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant  qdrant/qdrant:v1.9.0    Up 18 seconds (healthy)   6333-6334/tcp
redis   redis:7-alpine          Up 18 seconds (healthy)   6379/tcp
```

**Architecture diagram:**

```text
                     host :80 / :443
 Client (curl) ─────────────────────────┐
                                        ▼
                     ┌───────────────────────────────────┐
                     │ nginx (nginx:alpine)              │
                     │ reverse proxy + LB (upstream      │
                     │ agent_backend), rate limit 10r/s  │
                     │ burst 20, security headers        │
                     └──────────────────┬────────────────┘
                                        │ http://agent:8000
          ──────────── network "internal" (bridge) ───────────────
                                        ▼
                     ┌───────────────────────────────────┐
                     │ agent (FastAPI, multi-stage image)│
                     │ uvicorn 2 workers, không publish  │
                     │ port ra host, HEALTHCHECK /health │
                     └────────┬─────────────────┬────────┘
            REDIS_URL=        │                 │  QDRANT_URL=
            redis://redis:6379/0                   http://qdrant:6333
                              ▼                 ▼
                ┌──────────────────────┐  ┌──────────────────────┐
                │ redis:7-alpine       │  │ qdrant v1.9.0        │
                │ session, rate limit  │  │ vector DB cho RAG    │
                │ volume redis_data    │  │ volume qdrant_data   │
                └──────────────────────┘  └──────────────────────┘
```

**Cách các service giao tiếp:**
- Cả 4 service nằm chung network bridge `internal`; Docker DNS phân giải tên service thành IP (đã kiểm từ trong nginx: `agent → 172.23.0.4`, `redis → 172.23.0.2`, `qdrant → 172.23.0.3`).
- Chỉ **nginx** publish cổng ra host (80/443). Agent, Redis, Qdrant không mở cổng ra ngoài, client chỉ vào được qua nginx.
- `depends_on: condition: service_healthy`: agent chỉ start sau khi Redis và Qdrant pass healthcheck; nginx start sau agent.
- Agent nhận địa chỉ Redis/Qdrant qua biến môi trường (`REDIS_URL`, `QDRANT_URL`), không hardcode. (Ghi chú: `main.py` của ví dụ này mới chỉ khai báo biến, chưa thực sự gọi Redis/Qdrant; Redis được dùng thật ở Part 5 và Part 6.)

**Test qua Nginx:**

```text
$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"status":"ok","uptime_seconds":6.3,"version":"2.0.0","timestamp":"2026-11-25T09:18:45.746771"}

$ curl -i -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY

{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

Header `Server: nginx` và các security header chứng tỏ request đi qua nginx chứ không gọi thẳng agent.

**Thử rate limit của nginx:** gửi 40 request **song song** tới `/ask` → `27 × 200`, `13 × 429` (nginx `limit_req` 10r/s, burst 20). Gửi 40 request tuần tự thì cả 40 đều 200 vì tốc độ thực tế chỉ khoảng 5 req/s, dưới ngưỡng.

Dọn stack: `docker compose -p k5-day14-docker down`.

## Part 3: Cloud Deployment

> Theo lộ trình K5, Part 3 làm sau Part 6 và deploy chính agent Part 6 (`06-lab-complete/`). Chi tiết: [DEPLOYMENT.md](DEPLOYMENT.md).

### Exercise 3.1 / 3.2: Deploy

- **Railway (3.1), đã thử:** cài Railway CLI 5.75.0, `railway login` thành công, nhưng `railway init --name day14-agent` báo *"Your trial has expired. Please select a plan to continue using Railway."* Tài khoản hết hạn dùng thử nên không tạo được project nếu không lên gói trả phí.
- **Render (3.2), nền tảng chính:** Blueprint `06-lab-complete/render.yaml` tạo 2 tài nguyên Free: web service `day14-agent` (Docker) và Key Value `day14-redis`.
- **URL:** https://day14-agent-l0rz.onrender.com
- **Screenshot:** [dashboard](screenshots/dashboard.png) · [running](screenshots/running.png) · [test](screenshots/test.png)

```text
$ curl https://day14-agent-l0rz.onrender.com/health
{"status":"ok","version":"1.0.0","environment":"production","uptime_seconds":137.7}
$ curl https://day14-agent-l0rz.onrender.com/ready
{"ready":true}
$ curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'
401
$ curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'
200
```

4 lệnh tự kiểm CP6: `200 200 401 200` ([test.png](screenshots/test.png)). `AGENT_API_KEY` do Render sinh (`generateValue: true`), `REDIS_URL` lấy từ Key Value `day14-redis` (`fromService`), không có giá trị nào trong code.

### So sánh `railway.toml` và `render.yaml` (nền tảng còn lại)

So sánh hai file đã viết cho agent Part 6: [`06-lab-complete/railway.toml`](06-lab-complete/railway.toml) và [`06-lab-complete/render.yaml`](06-lab-complete/render.yaml).

| Khía cạnh | `railway.toml` | `render.yaml` | Vì sao khác |
|---|---|---|---|
| Phạm vi | Config **một service** (build + deploy). Project, Redis, domain tạo bằng CLI/dashboard (`railway init`, `railway add --database redis`, `railway domain`) | **Blueprint cả hạ tầng**: web service + Key Value Redis + biến môi trường + liên kết giữa chúng, trong một file | Railway chọn "config-as-code cho service, hạ tầng thao tác bằng CLI"; Render chọn Infrastructure-as-Code đầy đủ, tạo lại được toàn bộ stack từ file |
| Build | `builder = "DOCKERFILE"`, `dockerfilePath` | `runtime: docker`, `rootDir`, `dockerContext`, `dockerfilePath` | Render đọc Blueprint từ repo nên cần `rootDir` để biết app nằm trong `06-lab-complete/`; Railway thì `railway up` upload thẳng thư mục hiện tại |
| Health check | `[deploy] healthcheckPath = "/health"`, `healthcheckTimeout = 100` | `healthCheckPath: /health` | Giống nhau về ý nghĩa: deploy mới chỉ nhận traffic khi `/health` trả 200 (zero-downtime) |
| Restart | `restartPolicyType = "ON_FAILURE"`, `restartPolicyMaxRetries = 3` | Không khai báo, Render tự restart | Railway cho chỉnh chính sách restart; Render quản lý ngầm |
| Secret | **Không ghi trong file**, đặt bằng `railway variables` / dashboard; Redis tham chiếu bằng `${{Redis.REDIS_URL}}` | Khai báo **tên** biến trong file nhưng không ghi giá trị: `generateValue: true` (Render tự sinh `AGENT_API_KEY`), hoặc `sync: false` (nhập tay) | Render muốn file mô tả đủ hạ tầng nên có cơ chế riêng để khai báo secret mà không lộ giá trị |
| Redis | Thêm bằng lệnh `railway add --database redis`, nối qua reference variable | Khai báo `type: keyvalue` ngay trong file, `REDIS_URL` lấy bằng `fromService ... property: connectionString`, `ipAllowList: []` (chỉ nội bộ) | Cùng mục tiêu: agent không hardcode địa chỉ Redis |
| PORT | Railway inject `PORT`; Dockerfile `CMD` dùng `${PORT:-8000}` | Render inject `PORT` (mặc định 10000), cùng `CMD` đó | Một Dockerfile chạy được ở cả hai nhờ đọc `PORT` từ env (12-factor) |
| Plan / region | Chọn trên dashboard | `plan: free`, `region: singapore` trong file | Render đưa cả quy mô, vị trí vào IaC |

**Tóm lại:** `railway.toml` ngắn vì chỉ mô tả *cách build/chạy một service*, phần còn lại làm bằng CLI; `render.yaml` dài hơn vì mô tả *toàn bộ hạ tầng* (service + database + nối dây + secret) để Render dựng lại y hệt từ Git. Cả hai đều không chứa giá trị secret, đều dùng chung một Dockerfile.

### Exercise 3.3: Cloud Run CI/CD (`03-cloud-deployment/production-cloud-run/`)

`cloudbuild.yaml` là pipeline Cloud Build chạy khi push lên `main`, gồm 4 bước nối tiếp nhau (`waitFor`):
1. **test**: trong image `python:3.11-slim`, cài `requirements.txt` + `pytest` rồi chạy `pytest tests/`; test fail thì dừng pipeline, không build.
2. **build**: `docker build` gắn 2 tag `gcr.io/$PROJECT_ID/ai-agent:$COMMIT_SHA` và `:latest`, dùng `--cache-from ...:latest` để tận dụng layer cache.
3. **push**: đẩy cả hai tag lên Container Registry.
4. **deploy**: `gcloud run deploy ai-agent` với image tag `$COMMIT_SHA` (mỗi bản deploy truy được về đúng commit, dễ rollback), region `asia-southeast1`, `--min-instances=1` (tránh cold start), `--max-instances=10`, 512Mi / 1 CPU, `ENVIRONMENT=production`, và `OPENAI_API_KEY` lấy từ **Secret Manager** (`--set-secrets`), không nằm trong code.

`service.yaml` mô tả cùng service dưới dạng Knative (IaC, áp dụng bằng `gcloud run services replace`): autoscale 1–10 instance, mỗi instance tối đa 80 request đồng thời (`containerConcurrency: 80`), timeout 60 s, `livenessProbe` gọi `/health`, `startupProbe` gọi `/ready`, secret `OPENAI_API_KEY` / `AGENT_API_KEY` lấy qua `secretKeyRef`.


## Part 4: API Security

### Exercise 4.1: API Key authentication (`04-api-gateway/develop/app.py`)

- **Key được check ở đâu:** trong dependency `verify_api_key()` (dòng 39–54). Header được đọc bằng `APIKeyHeader(name="X-API-Key", auto_error=False)`; endpoint `/ask` gắn `Depends(verify_api_key)` nên key được kiểm tra **trước khi** vào thân hàm (trước khi gọi LLM). `/` và `/health` để public.
- **Sai key thì sao:** thiếu header → **401** `Missing API key`; có header nhưng sai → **403** `Invalid API key.` Request bị chặn, không tốn tiền LLM.
- **Rotate key:** key đọc từ env `AGENT_API_KEY` (dòng 35), không nằm trong code. Rotate = đặt giá trị mới trên platform rồi restart/redeploy, không sửa code. Muốn rotate không downtime thì cho phép tạm thời 2 key (cũ + mới, ví dụ `AGENT_API_KEYS=old,new`), báo client chuyển sang key mới, rồi gỡ key cũ.
- **Điểm yếu:** so sánh `api_key != API_KEY` không phải constant-time (nên dùng `hmac.compare_digest`), và default `"demo-key-change-in-production"` khiến app vẫn chạy khi quên đặt env (nên fail-fast).

Chạy `AGENT_API_KEY=secret-key-123 python app.py`:

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
content-type: application/json

{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
content-type: application/json

{"detail":"Invalid API key."}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: ***" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
content-type: application/json

{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

### Exercise 4.2: JWT authentication (`04-api-gateway/production/auth.py`)

**JWT flow:**
1. Client `POST /auth/token` với username/password → `authenticate_user()` kiểm tra → `create_token()` ký payload `{sub, role, iat, exp}` bằng HS256 với `JWT_SECRET` (lấy từ env), hạn 60 phút.
2. Client gửi `Authorization: Bearer <token>` ở mọi request.
3. `verify_token()` kiểm chữ ký + hạn, lấy `username`, `role` từ payload. Server **không cần tra DB** mỗi request (stateless), nên instance nào cũng verify được miễn là dùng chung secret.
4. Không có token → 401; token hết hạn → 401; token sai chữ ký/sai định dạng → 403.

```text
$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"wrong"}'
HTTP/1.1 401 Unauthorized
{"detail":"Invalid credentials"}

$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
HTTP/1.1 200 OK
x-content-type-options: nosniff
x-frame-options: DENY
x-xss-protection: 1; mode=block
referrer-policy: strict-origin-when-cross-origin

{"access_token":"eyJ***MASKED***","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJ***MASKED***"}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer abc.def.ghi" -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid token."}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 200 OK
{"question":"Explain JWT","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.","usage":{"requests_remaining":9,"budget_remaining_usd":1.9e-05}}
```

### Exercise 4.3: Rate limiting (`04-api-gateway/production/rate_limiter.py`)

- **Algorithm:** **sliding window log**. Mỗi user có một `deque` timestamp; mỗi request xoá các timestamp cũ hơn 60 giây, nếu số còn lại ≥ limit thì trả 429, ngược lại thêm `now` vào deque. (Docstring gọi là "Sliding Window Counter", nhưng thực chất lưu từng timestamp nên là sliding *log*: chính xác, không bị burst ở ranh giới cửa sổ như fixed window, đổi lại tốn bộ nhớ O(limit) mỗi user.)
- **Limit:** user thường (`student`) **10 request / 60 giây**; admin (`teacher`) 100 request / 60 giây.
- **Bypass cho admin:** `app.py` dòng 141 chọn `rate_limiter_admin if role == "admin" else rate_limiter_user`. Role nằm trong JWT đã ký nên client không tự sửa được. Muốn bỏ hẳn giới hạn cho admin thì bỏ qua `limiter.check()` khi `role == "admin"`, nhưng vẫn nên giữ một limit cao để chống token admin bị lộ.
- **Hạn chế:** state nằm trong RAM của process, nên chạy nhiều instance/worker thì mỗi cái đếm riêng, restart là reset. Production phải đưa vào Redis (làm ở Part 6).

Vòng lặp 20 request với token `student` (đã ẩn phần `answer`; request đầu tiên đã dùng ở bài 4.2):

```text
req  1: {"question":"Test 1","usage":{"requests_remaining":8,...}}  [HTTP 200]
req  2: {"question":"Test 2","usage":{"requests_remaining":7,...}}  [HTTP 200]
...
req  9: {"question":"Test 9","usage":{"requests_remaining":0,...}}  [HTTP 200]
req 10: {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":52}}  [HTTP 429]
req 11: {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":52}}  [HTTP 429]
...
req 20: {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":49}}  [HTTP 429]

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" ... -d '{"question":"one more"}'
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795598604
retry-after: 49
content-type: application/json

{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":49}}
```

Tổng cộng 10 request thành công trong 60 giây (1 ở bài 4.2 + 9 ở vòng lặp), từ request thứ 11 trả **429** kèm `Retry-After`. `GET /me/usage` xác nhận `"requests":10`.

**Hai lỗi nhỏ phát hiện khi chạy:** (1) field `budget_remaining_usd` trong response `/ask` thực ra là số tiền **đã tiêu** (`usage.total_cost_usd`) chứ không phải số còn lại; (2) middleware xoá header `server`, nhưng uvicorn thêm header này sau middleware, nên response vẫn có `server: uvicorn` (muốn ẩn thật phải chạy `uvicorn --no-server-header`).

### Exercise 4.4: Cost guard implementation

Code: [`04-api-gateway/production/cost_guard_redis.py`](04-api-gateway/production/cost_guard_redis.py).

**Cách làm:**
- **Key theo user và theo tháng:** `budget:<user_id>:<YYYY-MM>` (giờ UTC). Sang tháng mới thì tự dùng key mới bắt đầu từ $0, không cần cron reset. Key được đặt `EXPIRE` 32 ngày để key tháng cũ tự xoá.
- **Budget đọc từ env** `MONTHLY_BUDGET_USD` (mặc định $10), Redis lấy từ `REDIS_URL`.
- **Kiểm tra và trừ tiền là một thao tác atomic:** dùng Lua script chạy trên Redis (`GET` → so với budget → `INCRBYFLOAT` → `EXPIRE`). Lời giải mẫu làm `GET` rồi `INCRBYFLOAT` ở hai lệnh riêng nên có race condition: hai request đồng thời cùng đọc thấy "còn tiền" rồi cùng cộng, vượt budget. Lua script chạy nguyên khối nên không bị.
- **Vì sao dùng Redis thay vì dict trong RAM** (như `cost_guard.py`): nhiều instance dùng chung một con số chi tiêu; restart/deploy không làm reset budget.
- **Gắn vào API:** gọi `check_budget(user, estimated_cost)` **trước** khi gọi LLM; `False` → trả **402 Payment Required**. Rate limit chặn *tốc độ* (chống spam/DoS), cost guard chặn *tổng tiền* (một user gọi chậm nhưng đều, hoặc prompt rất dài, vẫn có thể đốt hết budget mà không chạm rate limit), nên cần cả hai.

Test với Redis thật (`docker run -d -p 6380:6379 redis:7-alpine`):

```text
$ REDIS_URL=redis://localhost:6380/0 python cost_guard_redis.py
budget/tháng = $10.0, key = budget:selftest-user:2026-11
4 request x $3.00 -> [True, True, True, False]   (đã tiêu $9.00)
thêm $1.00 -> True   (đã tiêu $10.00)
thêm $0.01 -> False   (đã tiêu $10.00)
TTL của key = 2764800 giây (~32 ngày)

# 50 thread gọi check_budget cùng lúc
50 request đồng thời x $0.50, budget $10 -> cho qua 20, chặn 30, đã tiêu $10.00
```

Request thứ 4 ($3, tổng sẽ là $12) bị chặn nhưng request $1 sau đó (tổng đúng $10) vẫn qua, tức là chặn theo "sẽ vượt" chứ không theo "đã vượt". Với 50 request song song, đúng 20 × $0.50 = $10 được cho qua, không vượt budget dù chỉ 1 cent.

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop/app.py`)

File develop đã có sẵn hai endpoint; mình đọc, chạy và kiểm tra:

- **`GET /health` (liveness, dòng 104–144):** chỉ trả lời "process còn sống không". Trả 200 kèm uptime, version, và check bộ nhớ bằng `psutil` (`degraded` nếu RAM > 90%). Không kiểm tra Redis/DB, vì nếu dependency chết mà liveness trả lỗi thì platform sẽ restart agent liên tục, dù restart không sửa được Redis.
- **`GET /ready` (readiness, dòng 147–168):** trả **503** khi `_is_ready = False`, tức lúc đang khởi động (trước khi lifespan startup xong) hoặc đang shutdown; trả 200 khi sẵn sàng. Load balancer dùng nó để quyết định có gửi traffic vào instance hay không. Bản production (dòng 208–215) còn `ping` Redis, Redis chết thì 503.
- **Khác biệt:** liveness fail → **restart** container; readiness fail → **ngừng gửi traffic** nhưng không restart. Vì vậy `/health` vẫn 200 khi Redis chết còn `/ready` phải 503 (đúng yêu cầu của Part 6).

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.7,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T09:32:02.473727+00:00","checks":{"memory":{"status":"ok","used_percent":55.8}}}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true,"in_flight_requests":1}

$ time curl -X POST "http://localhost:8000/ask?question=hello"
{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
real    0m3.313s        # mock LLM giả lập độ trễ 3 giây
```

(`in_flight_requests` = 1 vì middleware đếm luôn chính request `/ready`.)

### Exercise 5.2: Graceful shutdown

Cơ chế trong `develop/app.py`:
1. `uvicorn.run(..., timeout_graceful_shutdown=30)`: khi nhận SIGTERM, uvicorn **ngừng nhận kết nối mới** nhưng để request đang chạy hoàn thành (tối đa 30 giây).
2. Middleware `track_requests` đếm `_in_flight_requests`.
3. `lifespan` phần shutdown: đặt `_is_ready = False` (để `/ready` trả 503, LB ngừng gửi traffic), chờ `_in_flight_requests` về 0 (tối đa 30 giây), rồi mới đóng kết nối và thoát.
4. `signal.signal(SIGTERM, handle_sigterm)` chỉ để log.

**Test:** Windows không gửi được SIGTERM, nên mình chạy app trong container Linux (`python:3.11-slim`, `exec python app.py` để app là PID 1), gửi một request `/ask` (mất ~3 giây), sau 0,5 giây thì `docker stop` (gửi SIGTERM):

```text
Request đang chạy khi gửi SIGTERM:
{"answer":"Đây là câu trả lời từ AI agent (mock). ..."}  [HTTP 200, 3.053107s]
docker stop mất: 3.07s
ExitCode=0

--- log container:
INFO:     Shutting down                                      ← nhận SIGTERM, ngừng nhận kết nối mới
INFO:     172.17.0.1:43330 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK   ← request đang chạy vẫn xong
INFO:     Waiting for application shutdown.
2026-11-25 09:32:43,239 INFO 🔄 Graceful shutdown initiated...
2026-11-25 09:32:43,240 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [1]
2026-11-25 09:32:43,240 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

**Kết luận:** request đang xử lý vẫn trả **200**, container tự thoát với **exit code 0** sau 3,07 giây (đúng bằng thời gian request còn lại), không phải chờ hết 30 giây rồi bị SIGKILL (exit 137).

**Quan sát thêm:** dòng `Received signal 15` của `handle_sigterm` in ra **cuối cùng**. Lý do: `uvicorn.run()` tự cài signal handler đè lên handler của app, và chỉ gọi lại handler cũ sau khi đã tắt xong. Vậy cleanup thật phải đặt trong `lifespan` (phần sau `yield`), không đặt trong signal handler tự viết. Một handler tự viết mà gọi `sys.exit()` ngay còn làm mất request đang chạy.

### Exercise 5.3: Stateless design (`05-scaling-reliability/production/app.py`)

Bản production đã stateless:
- Session / conversation history lưu ở Redis, key `session:<session_id>`, `SETEX` TTL 3600 giây (dòng 59–90), giữ tối đa 20 message.
- `/chat` nhận `session_id`, đọc history từ Redis, thêm câu hỏi + câu trả lời rồi ghi lại. Không có biến global nào giữ dữ liệu người dùng.
- Mỗi response có `served_by` (ID ngẫu nhiên của instance) để thấy request đi qua instance nào.

**Vì sao phải stateless:** mỗi instance có RAM riêng. Nếu `conversation_history = {}` nằm trong RAM, request 1 vào instance A lưu history ở A, request 2 bị LB chuyển sang B thì B không có history; instance bị restart/scale down thì mất hết. Đưa state ra Redis thì instance nào cũng đọc được, và có thể thêm/bớt/khởi động lại instance thoải mái.

**Hạn chế vẫn còn:** (1) nếu Redis không kết nối được lúc khởi động, app **lặng lẽ** chuyển sang dict trong RAM (dòng 42–45), nên dễ chạy sai mà không biết; production nên fail-fast. (2) `append_to_history` làm GET → sửa → SET không atomic, hai request cùng session song song có thể ghi đè nhau (dùng `RPUSH` + `LTRIM` sẽ an toàn hơn). (3) field `turn` lệch 1 (đọc history *sau khi* đã thêm câu hỏi rồi lại +1), thấy ở kết quả 5.5: request thứ 6 báo `turn 7`.

### Exercise 5.4: Load balancing

```bash
cd 05-scaling-reliability/production
touch .env.local
docker compose -p k5-day14-scaling up -d --build --scale agent=3
```

```text
k5-day14-scaling-agent-1   Up 5 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-2   Up 6 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-3   Up 6 seconds (healthy)   8000/tcp
k5-day14-scaling-nginx-1   Up 5 seconds             0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 17 seconds (healthy)  6379/tcp
```

10 request `POST /chat` qua Nginx (`localhost:8080`), in `served_by` và header `X-Served-By`:

```text
req 1 -> instance-5b04fd redis      X-Served-By: 172.23.0.4:8000
req 2 -> instance-c3709e redis      X-Served-By: 172.23.0.3:8000
req 3 -> instance-989bda redis      X-Served-By: 172.23.0.5:8000
req 4 -> instance-5b04fd redis      X-Served-By: 172.23.0.4:8000
req 5 -> instance-c3709e redis      X-Served-By: 172.23.0.3:8000
req 6 -> instance-989bda redis      X-Served-By: 172.23.0.5:8000
req 7 -> instance-5b04fd redis      X-Served-By: 172.23.0.4:8000
req 8 -> instance-c3709e redis      X-Served-By: 172.23.0.3:8000
req 9 -> instance-989bda redis      X-Served-By: 172.23.0.5:8000
req 10 -> instance-5b04fd redis     X-Served-By: 172.23.0.4:8000
```

`docker compose -p k5-day14-scaling logs agent`: agent-1 xử lý 3, agent-2 xử lý 4, agent-3 xử lý 3 request. Nginx phân phối **round-robin** đều cho 3 instance: tên `agent` trong `upstream` được Docker DNS phân giải thành 3 IP lúc Nginx khởi động, Nginx coi đó là 3 server.

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
Session ID: 8351751f-9d33-4690-8697-b69cdab054aa

Request 1: [instance-c3709e]   Q: What is Docker?
Request 2: [instance-989bda]   Q: Why do we need containers?
Request 3: [instance-5b04fd]   Q: What is Kubernetes?
Request 4: [instance-c3709e]   Q: How does load balancing work?
Request 5: [instance-989bda]   Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-5b04fd', 'instance-c3709e', 'instance-989bda'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  ...
  [user]: What is Redis used for?...
  [assistant]: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã đư...

✅ Session history preserved across all instances via Redis!
```

**Giải thích:** 5 câu của **cùng một session** được 3 instance khác nhau xử lý (round-robin), nhưng history đọc lại vẫn đủ **10 message** (5 câu hỏi + 5 câu trả lời) theo đúng thứ tự. Mỗi instance đọc history mà instance trước đã ghi vào Redis. Nếu history nằm trong RAM thì mỗi instance chỉ thấy phần của mình (1–2 câu) và câu hỏi tiếp theo sẽ "quên" ngữ cảnh.

**Thử thêm: tắt 1 instance giữa chừng** (`test_stateless.py` không tự tắt instance nên mình tự làm). `docker stop k5-day14-scaling-agent-2` rồi gửi tiếp 4 câu vào cùng session:

```text
200 0.121458s  req 6 -> instance-c3709e turn 7
200 0.159353s  req 7 -> instance-989bda turn 8
200 38.934953s req 8 -> instance-c3709e turn 9     ← chậm
200 0.143369s  req 9 -> instance-989bda turn 10
history count = 18                                  ← 9 câu hỏi + 9 trả lời, không mất gì
```

Nginx log: `connect() failed (113: Host is unreachable) while connecting to upstream ... upstream: "http://172.23.0.4:8000/chat"`.

- **Session không mất:** 2 instance còn lại phục vụ tiếp, history vẫn đủ 18 message. Đây đúng là lợi ích của stateless.
- **Nhưng có 1 request chậm 38,9 giây:** Nginx vẫn round-robin vào IP của container đã tắt, chờ connect timeout rồi mới `proxy_next_upstream` sang instance khác. Request không lỗi (vẫn 200) nhưng người dùng phải chờ gần 40 giây. Cách giảm: đặt `proxy_connect_timeout 2s;` và `server agent:8000 max_fails=1 fail_timeout=10s;` để Nginx loại instance chết ngay sau lần lỗi đầu. Load balancer thật (K8s Service, Railway, Render) dùng readiness probe để loại instance trước khi gửi traffic.

Dọn stack: `docker compose -p k5-day14-scaling down`.

## Part 6: Final Project (`06-lab-complete/`)

Đã làm hết các TODO theo thứ tự gợi ý trong `06-lab-complete/README.md`:

| File | Nội dung |
|---|---|
| `app/config.py` | Đọc `PORT`, `ENVIRONMENT`, `AGENT_API_KEY`, `REDIS_URL`, `RATE_LIMIT_PER_MINUTE`, `DAILY_BUDGET_USD`, `MONTHLY_BUDGET_USD`, `LOG_LEVEL`, `APP_VERSION` từ env; `ENVIRONMENT=production` mà thiếu key thì dừng ngay lúc khởi động |
| `app/auth.py` | `X-API-Key` so sánh bằng `hmac.compare_digest`; thiếu / sai / chưa cấu hình key → 401 |
| `app/redis_client.py` | Client lazy (không ping lúc import → app vẫn lên khi chưa có Redis), timeout 1–2 s để `/ready` trả 503 nhanh |
| `app/rate_limiter.py` | Sliding window log bằng sorted set trong Redis, chạy trong Lua script (atomic giữa nhiều worker / instance) → 429 + `Retry-After` |
| `app/cost_guard.py` | Cộng chi phí theo ngày + tháng vào Redis (key theo ngày/tháng, có TTL) → 402 khi hết budget |
| `app/main.py` | `/health` (luôn 200), `/ready` (503 khi mất Redis / đang tắt), log JSON một object mỗi dòng, lifespan shutdown (không đè signal handler của uvicorn), Redis lỗi → 503, lỗi lạ → 500 không lộ stack trace |
| `Dockerfile` | Multi-stage (venv ở builder), `python:3.11-slim`, user `app` non-root, `HEALTHCHECK`, `CMD sh -c "exec uvicorn ... --port ${PORT:-8000}"` → uvicorn là PID 1. Image **182.9 MB** |
| `docker-compose.yml`, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml` | Đủ theo hợp đồng chấm |

**Kết quả `check_production_ready.py`** (chạy đầy đủ, không cờ, trong **Ubuntu 24.04 trên WSL2**, Python 3.12.3, `os.name = posix`, Docker Desktop 29.5.2 bật WSL integration):

```text
$ cd 06-lab-complete && python check_production_ready.py
...
✅ 11. Graceful shutdown
     ✅ Code xử lý SIGTERM / lifespan shutdown
     ✅ Khi chạy: SIGTERM lúc đang có request → request vẫn 200, process tự thoát — exit code 0
     ✅ Docker: `docker stop` tắt êm (exit code 0, < 10s) — exit code 0, 0.7s
...
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Một số dòng chi tiết từ cùng lần chạy: image build 153 s, **183 MB**; `429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)` với 2 worker (rate limit dùng chung qua Redis); restart agent vẫn 429 (stateless); Redis mất kết nối → `/ready` 503, `/health` 200; budget ≈ 0 → `402 ở request thứ 1`; 15 dòng log JSON hợp lệ.

Trước đó, chạy trên Windows (Python cho Windows) cho 11/12 vì tiêu chí 11 báo ⚠️ *"Windows không gửi được SIGTERM"*, đúng như lưu ý số 2 trong README. Ngoài ra `--public-url https://day14-agent-l0rz.onrender.com` → ✅ `GET /health → 200`.

## AI log

Toàn bộ bài làm với sự hỗ trợ của **Claude Code** (Anthropic) chạy trong VS Code, trên máy và tài khoản của tôi. Claude đọc đề, chạy lệnh, viết code và báo cáo; tôi duyệt từng checkpoint trước khi đi tiếp, tự đăng nhập các dịch vụ và tự chụp màn hình.

| Phần | Tôi yêu cầu | AI làm / đề xuất | Tôi kiểm tra / tự làm |
|---|---|---|---|
| CP0 | Tóm tắt đề, kiểm tra môi trường | Phát hiện Docker Hub hết lượt pull ẩn danh và thiếu Ubuntu WSL (ảnh hưởng tiêu chí 11); sau đó cài Ubuntu 24.04 (`wsl --install --no-launch`) và venv trong đó | Tự `docker login`, tự bật WSL integration trong Docker Desktop |
| Part 1, 2, 4, 5 | Chạy ví dụ và trả lời câu hỏi | Chạy app/container/stack, đo image, gửi request, viết câu trả lời từ output thật; phát hiện lỗi encoding cp1252 trên Windows, thứ tự `logging.basicConfig`, độ trễ 39 s khi 1 instance chết sau Nginx | Đọc lại từng câu trả lời và đối chiếu với output |
| Part 4.4 | Cost guard Redis | Viết `cost_guard_redis.py` dùng Lua để atomic; test 50 request song song (đúng 20 × $0.5 = $10 được qua) | Xem kết quả test |
| Part 6 | Sửa TODO tới PASS | Viết toàn bộ `app/*.py`, Dockerfile, compose, config deploy; chạy script chấm (11/12 trên Windows) và kiểm tiêu chí SIGTERM trong container Linux | Đọc code; script chấm không bị sửa |
| CP6 | Deploy | Thử Railway (trial hết hạn) → chuyển sang Render, sửa `render.yaml`, commit + push (khi tôi đồng ý), kiểm tra URL | Tự đăng nhập Railway, cài GitHub App, tạo Blueprint trên Render, tự lấy `AGENT_API_KEY` và chạy 4 lệnh test (key không gửi qua AI) |
| Part 7 | Benchmark + observability | Chạy load test / eval, sửa `KNOWLEDGE_BASE` (không sửa `golden_dataset.jsonl`), viết job CI, chạy Locust + 5 thí nghiệm, đo thời gian alert bằng Prometheus API, tìm trace bằng Jaeger API; sửa lỗi `PYTHONUSERBASE` trong Dockerfile của stack | Tự chụp ảnh Grafana / Jaeger / Prometheus; số trong `BENCHMARK_REPORT.md` lấy từ file JSON/CSV trong `reports/` và `benchmark-results/` |

Không dùng AI để bịa số đo: mọi con số trong bài đều lấy từ lệnh đã chạy và file report đã nộp.
