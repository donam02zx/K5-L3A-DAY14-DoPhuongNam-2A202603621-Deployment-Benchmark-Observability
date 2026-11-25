# Benchmark & Observability Report — Day 14

> Mọi con số lấy từ report JSON trong `reports/` (load test, eval) và CSV trong `benchmark-results/` (Locust).

**Họ tên:** Do Phuong Nam — 2A202603621
**Ngày chạy:** 2026-11-25
**Môi trường:** Laptop Windows 11 Home, Intel Core i7-9750H (6 nhân / 12 luồng, 2.6 GHz), RAM 32 GB, Python 3.14.6 (venv), Docker Desktop 29.5.2 (WSL2). Load generator (`load_test.py`) và agent chạy trên **cùng một máy**, agent là 1 process uvicorn (1 event loop).

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | LLM mock trả lời trong ~160–240 ms ở tải thường (p50 ≈ 200 ms ở mọi mức tải chưa quá tải). 500 ms ≈ 2,5 lần p50, đủ chỗ cho jitter và retrieval mà người dùng vẫn thấy "tức thì"; đây cũng là ngưỡng mặc định của `load_test.py`. Đặt theo p95 chứ không theo trung bình để bắt được tail latency (xem 2.2). |
| Error rate `/ask` | < 2 % | Upstream LLM mặc định lỗi ~1% (`MOCK_ERROR_RATE=0.01`), đo được 0,3–2,3%. 2% cho error budget gấp đôi mức nền: một sự cố thật (vd LLM lỗi 20%) vượt ngưỡng ngay, còn dao động ngẫu nhiên thì không báo động giả liên tục. |
| Accuracy trên golden dataset | ≥ 85 % | Bằng ngưỡng của job CI (`--min-accuracy 0.85`): cho phép tối đa 3/23 câu sai khi thêm tính năng mới, nhưng chặn mọi thay đổi làm agent tụt về mức v1 (4,3%). Kèm điều kiện **không regression**: câu đã đúng ở baseline không được sai. |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

Lệnh: `python load_test.py --url http://localhost:8002 --requests 300 --concurrency <c> --out reports/load_v2_c<c>.json` (agent v2, cấu hình mặc định).

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 3.79 | 201.6 | 237.5 | 1726.8 | 1.00% | ✅ PASS |
| 10 | 36.88 | 200.8 | 309.6 | 1717.6 | 1.67% | ✅ PASS |
| 50 | 102.85 | 215.4 | 542.4 | 1779.2 | 2.33% | ❌ FAIL (p95 và error rate) |
| 100 | 45.44 | 686.7 | 4235.7 | 4526.9 | 0.33% | ❌ FAIL (p95) |

**Nhận xét:** RPS tăng **gần tuyến tính** từ concurrency 1 → 10 (3,79 → 36,88 req/s, ×9,7) vì mỗi request chủ yếu là *chờ* (`asyncio.sleep` giả lập LLM ~200 ms) chứ không tốn CPU, nên một event loop xử lý song song được nhiều request; p50 gần như không đổi (~200 ms). Theo Little's law, RPS ≈ concurrency / latency = 10 / 0,25 s ≈ 40 req/s, khớp số đo.

Từ 10 → 50, RPS chỉ tăng ×2,8 (lên 102,85) chứ không ×5, và p90/p95 bắt đầu vượt 500 ms: process bắt đầu **bão hoà CPU**. Phần tốn CPU của mỗi request (parse JSON, Pydantic validate, ghi log JSON, cập nhật Prometheus metrics) đều chạy trên **một** event loop (1 process uvicorn = 1 nhân CPU), lại thêm load generator chạy trên cùng máy giành CPU.

Ở 100, hệ thống **quá tải**: RPS còn **giảm** xuống 45,44, p50 tăng gấp 3 (686,7 ms), p95 > 4 giây. Request xếp hàng chờ event loop, và chi phí mở 100 kết nối TCP cùng lúc cộng thêm vào. Chạy lại 2 lần nữa (không lưu file) cho kết quả tương tự: 56,27 và 60,07 req/s, p95 3972 và 4490 ms, nên đây là hiện tượng ổn định chứ không phải nhiễu. Error rate ở mức 100 thấp hơn (0,33%) chỉ là ngẫu nhiên của `MOCK_ERROR_RATE`, không phải hệ thống khoẻ hơn.

**Kết luận:** một instance chịu được khoảng **10–40 request đồng thời** mà vẫn đạt SLO. Muốn phục vụ nhiều hơn phải scale ngang (`--workers` hoặc thêm instance sau load balancer, xem mục 5), không phải chỉ tăng concurrency.

### 2.2 Tail latency

Khởi động lại agent v2 với từng giá trị `MOCK_SLOW_RATE`, mỗi lần `--requests 300 --concurrency 20`.

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 222.1 | 467.4 | 499.6 |
| 0.03 (mặc định) | 260.9 | 499.2 | 1711.5 |
| 0.1 | 364.3 | 1713.4 | 1745.7 |

(File: `reports/load_v2_slow_0.json`, `load_v2_slow_0.03.json`, `load_v2_slow_0.1.json`. p50 gần như không đổi: 206.1 / 204.3 / 209.1 ms.)

**Nhận xét:** mỗi request "slow" bị cộng thêm 1500 ms (`MOCK_SLOW_MS`).
- **avg** chỉ tăng từ 222 → 261 → 364 ms (+64% khi 10% request chậm). Trung bình "pha loãng" vài request rất chậm vào hàng trăm request nhanh, nên nhìn avg vẫn tưởng hệ thống ổn.
- **p99** nhảy ngay từ 500 → **1712 ms** khi chỉ 3% request chậm: chỉ cần > 1% request chậm là p99 rơi vào nhóm chậm.
- **p95** giữ ~500 ms ở 3% (vì 3% < 5%), nhưng khi 10% request chậm thì nhảy lên **1713 ms**, vượt SLO gấp 3,4 lần, trong khi avg vẫn dưới 500 ms.
- → **p99 nhạy nhất**, rồi tới p95; avg kém nhạy nhất. Percentile p*k* "bật" ngay khi tỉ lệ request chậm vượt (100 − k)%.

SLO không nên đặt theo average vì (1) avg giấu tail latency: ở `MOCK_SLOW_RATE=0.1`, avg 364 ms "đạt" ngưỡng 500 ms trong khi cứ 10 người dùng thì 1 người chờ gần 2 giây; (2) người dùng cảm nhận theo từng request chứ không theo trung bình, và người dùng nhiều request nhất (khách hàng quan trọng) gặp tail latency thường xuyên nhất; (3) percentile cho câu hỏi rõ ràng "95% request nhanh hơn X ms" để đặt error budget và alert.

Ghi chú: ở `MOCK_SLOW_RATE=0` vẫn có p95 = 467 ms dù không có request "slow" nào (max chỉ 509 ms, không có request nào bị cộng 1500 ms). Khả năng cao đó là thời gian xếp hàng lúc 20 kết nối đầu tiên được mở cùng lúc ngay sau khi agent vừa khởi động lại (warm-up). Vì vậy benchmark nghiêm túc nên có pha warm-up và bỏ qua những request đầu.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | 100.0% (23/23) |
| Avg tokens / câu | 56.5 | 109.1 | 116.9 |
| Tổng cost (USD) | 0.000396 | 0.000762 | 0.000818 |
| Cost / câu đúng (USD) | 0.00039615 | 0.00003627 | 0.00003557 |
| Quality gate | FAIL (cần ≥ 80%) | PASS (≥ 80%, 0 regression so với v1) | PASS (≥ 100%, 0 regression so với v2) |

Lệnh:

```bash
python eval/run_eval.py --url http://localhost:8001 --out reports/eval_v1.json                                     # exit 1
python eval/run_eval.py --url http://localhost:8002 --out reports/eval_v2.json --baseline reports/eval_v1.json      # exit 0
python eval/run_eval.py --url http://localhost:8002 --min-accuracy 1.0 --baseline reports/eval_v2.json --out reports/eval_v2_fixed.json   # exit 0
```

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| security-02 ("JWT khác gì API key trong xác thực?") | `token`, `hết hạn`, `chữ ký` | KB không có tài liệu nào chứa từ khoá khớp với câu hỏi nên v2 rơi về câu trả lời chung chung. Thêm entry `(["jwt", "json web token"], ...)` vào `KNOWLEDGE_BASE`: so sánh API key (chuỗi cố định, không tự hết hạn) với JWT (token có chữ ký số, chứa user/role và thời điểm hết hạn `exp`, server chỉ cần xác minh chữ ký). |
| scaling-03 ("Kubernetes HPA scale dựa trên những chỉ số nào?") | `cpu`, `memory`, `custom metrics` | Thêm entry `(["hpa", "autoscal", "kubernetes"], ...)`: HPA tăng giảm số pod theo CPU, memory hoặc custom metrics (RPS, độ dài hàng đợi) lấy từ metrics-server / Prometheus Adapter. |

Không sửa `golden_dataset.jsonl`. Từ khoá của 2 entry mới không trùng với câu hỏi nào khác trong dataset nên không làm đổi tài liệu được retrieve cho 21 câu đang đúng, và eval xác nhận **0 regression**. Sau khi sửa phải tắt và bật lại agent v2 (app không tự nạp lại code). Bản `production/utils/mock_llm.py` cũng được sửa giống hệt để stack Docker ở mục 4 cũng đạt 100%.

**Job CI chặn merge:** [`.github/workflows/eval.yml`](.github/workflows/eval.yml). Trên mỗi pull request sửa `benchmark-observability/`, job cài dependencies, bật agent v2 (`MOCK_ERROR_RATE=0`), chạy `run_eval.py --min-accuracy 0.85 --baseline reports/eval_v2_fixed.json`. `run_eval.py` trả **exit code 1** khi accuracy < ngưỡng **hoặc** có câu bị regression so với baseline (đã thấy thực tế: eval v1 → exit 1, eval v2 → exit 0). Exit code ≠ 0 làm step fail → job fail → khi bật *Require status checks to pass* cho job `eval` trong branch protection, nút merge bị khoá.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**
- **Chất lượng:** retrieval (RAG) đưa accuracy từ 4,3% lên 91,3%, rồi 100% chỉ bằng cách bổ sung tài liệu, không cần đổi model. Thiếu tài liệu trong KB là nguyên nhân chính của câu sai.
- **Chi phí:** v2 tốn gần **gấp đôi token mỗi câu** (109,1 so với 56,5) vì tài liệu được nhét vào prompt, nên tổng cost tăng 92% (0.000396 → 0.000762 USD). Nhưng **cost / câu đúng giảm ~11 lần** (0.00039615 → 0.00003627 USD), vì v1 tiêu tiền cho 22 câu trả lời vô dụng. "Cost / câu" khiến v2 trông đắt hơn; "cost / câu đúng" cho thấy v2 rẻ hơn nhiều trên mỗi đơn vị giá trị thực, nên đây là chỉ số đúng để so hai phiên bản. Bản đã sửa KB còn rẻ hơn nữa (0.00003557 USD / câu đúng).
- **Latency:** v2 thêm bước retrieval (~20 ms) và prompt dài hơn (+0,2 ms/token), nên p50 của eval tăng từ 163,2 → 214,5 ms. p95 của v1 trong lần eval (1506,2 ms) cao hơn chỉ vì trùng một request "slow" ngẫu nhiên (`MOCK_SLOW_RATE=0.03` trên 23 câu), không phải do v1 chậm hơn. Mẫu 23 câu quá nhỏ để so sánh p95; latency phải so bằng load test.
- **Kết luận:** v2 đáng đánh đổi thêm ~50 ms và gấp đôi token để có accuracy gấp 20 lần.

---

## 4. Observability stack (production)

Chạy trong `06-lab-complete/benchmark-observability/production/` với `docker compose -p k5-day14-observability ...`. Giờ trong mục này là **UTC** (giờ trên ảnh chụp là giờ Việt Nam, UTC+7).

**Sửa lỗi trước khi chạy được:** agent trong stack crash liên tục với `ModuleNotFoundError: No module named 'uvicorn'`. Nguyên nhân: Dockerfile tạo user bằng `useradd -d /app agent` nên `HOME=/app`, Python tìm user site-packages ở `/app/.local`, trong khi package được copy từ builder vào `/home/agent/.local`. Mình thêm `ENV PYTHONUSERBASE=/home/agent/.local` vào `production/Dockerfile` (giống cách Dockerfile ở Part 5 làm). Unit test alert rules: `promtool test rules /p/alert_rules_test.yml` → `SUCCESS`.

### 4.0 Exercise 7.4 — đọc dashboard (Locust 30 user, 2 agent, cấu hình mặc định)

Số liệu query từ Prometheus lúc 10:22:48 UTC:

| Chỉ số | Giá trị |
|---|---|
| Traffic `/ask` | 18.4 req/s |
| Error rate | 0.89% |
| p50 / avg / p95 / p99 | 178 ms / 257 ms / 299 ms / 1890 ms |
| Req/s theo instance | 172.23.0.4: 9.02 · 172.23.0.8: 9.33 |
| Tokens/s | input 1443 · output 768 |

1. **p95 ≈ 299 ms, avg ≈ 257 ms.** Hai số này gần nhau, nhưng p99 lên tới 1890 ms. Nghĩa là ~95% request đều nhanh và ổn định; phần chậm nằm ở ~3% đuôi (`MOCK_SLOW_RATE=0.03`, mỗi request chậm bị cộng 1500 ms). avg bị kéo lên khỏi p50 (178 ms) chính bởi phần đuôi đó, nhưng nhìn avg không biết có người chờ gần 2 giây; phải nhìn p99.
2. **Nginx chia gần đều:** 9.02 và 9.33 req/s (lệch ~3%), đúng kiểu round-robin. Panel *Requests/s theo instance* cho thấy hai đường sát nhau trước khi có thí nghiệm D.
3. **Input tokens/s gần gấp đôi output tokens/s (1443 so với 768)** vì prompt gửi lên LLM = system prompt + tài liệu retrieve được (v2/RAG) + câu hỏi, còn câu trả lời chỉ là một đoạn ngắn (mock trả về chính tài liệu). Với RAG, input luôn lớn hơn output. Giá output đắt gấp 4 lần input nên cost vẫn chia gần đều cho hai phần.
4. **`curl http://localhost:8080/metrics` → `404 Not Found`** vì Nginx chặn riêng `location = /metrics { return 404; }`. Metrics lộ thông tin nội bộ (route, model, chi phí, số instance) nên không public ra internet. Prometheus vẫn scrape được vì nó gọi thẳng từng container `agent:8000/metrics` trong mạng nội bộ `obs_net`, không đi qua Nginx.

**Phát hiện:** với 30 user và cấu hình mặc định, cost burn rate ổn định quanh **$2.03/giờ** (query lúc kết thúc), sát ngưỡng `LLMCostBurnRateHigh > $2/giờ`. Ngay sau benchmark 100 user, alert này firing sẵn vì cửa sổ `rate(...[5m])` còn chứa dữ liệu lúc tải cao. Ngưỡng $2/giờ đang bằng mức chi tiêu bình thường của lượng traffic này, nên alert sẽ nhấp nháy giả. Ngưỡng cost nên đặt theo budget thật, hoặc theo **cost / request** thay vì tổng tiền/giờ.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải: [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png) (Golden Signals: 19.1 req/s, error rate 19.66% đỏ, p95 286.86 ms, Instances = 1), thêm [`grafana-dashboard-llm.png`](screenshots/grafana-dashboard-llm.png) (LLM latency/token/cost) và [`grafana-dashboard-instances.png`](screenshots/grafana-dashboard-instances.png) (LLM calls, req/s theo instance, bảng version). Ảnh chụp ở trạng thái A + D đang bật.
- [x] Một trace chậm trong Jaeger: [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png), trace `7ac7e7a…`, 1.9 s
- [x] Một trace lỗi trong Jaeger: [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png), trace `799b8af…`, `POST /ask 503`, các span `agent.run` và `chat mock-llm-small` có dấu lỗi đỏ
- [x] Trang Alerts của Prometheus: [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png), **2 alert cùng FIRING**: `AgentHighErrorRate` và `AgentInstanceDown`

### 4.2 Thí nghiệm "phá hệ thống"

Locust chạy nền 30 user suốt thí nghiệm. Mỗi thí nghiệm: sửa `.env` → `docker compose -p k5-day14-observability up -d agent` (D thì `docker pause`). Thời gian đo bằng cách hỏi `GET /api/v1/alerts` của Prometheus mỗi 2 giây, tính từ lúc chạy lệnh đổi cấu hình. Mỗi thí nghiệm bắt đầu từ cấu hình mặc định.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (error rate đo được 21.1%) | **89 s** (đổi 10:22:59 → pending ~10:23:22 → firing 10:24:28) | *Error rate* (stat 19.66% đỏ), *LLM calls theo kết quả* (đường `error` lên ~4 req/s), *Requests/s theo status code* (đường 503) |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` (p95 = 1.92 s) | **150 s** (đổi 10:24:44 → pending 10:25:07 → firing 10:27:14) | *Latency /ask p50/p95/p99*: p95 1.92 s, p99 1.98 s trong khi p50 chỉ 0.22 s và avg 0.66 s |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` (lên $16.4/giờ) | **146 s** (đổi 10:27:22 → pending 10:27:42 → firing 10:29:48) | *Cost burn rate (USD/giờ)* vọt lên ~$17.7/giờ; *LLM latency p95 theo model*: large ~500 ms so với small ~200 ms (×2.5); p50 `/ask` 0.18 → 0.41 s |
| D | `docker pause k5-day14-observability-agent-1` | `AgentInstanceDown` | **41 s** (pause 10:30:02 → pending 10:30:09 → firing 10:30:43) | *Instances* = 1; *Requests/s theo instance*: một đường về 0, đường còn lại gánh ~19 req/s |
| E | `AGENT_VERSION=v1` | **Không có** (quan sát 3 phút, 10:31:01 → 10:34:22) | — | *Tokens/s* giảm một nửa (input 1443 → 706/s), *Phiên bản đang chạy* đổi sang v1; p95 còn **giảm** 0.30 → 0.20 s, nhìn dashboard còn "khoẻ" hơn |

**Phân tích:**
- Thời gian tới lúc firing = (thời gian tạo lại container + DNS refresh 10 s) + (thời gian để `rate(...[1m])` / `rate(...[5m])` vượt ngưỡng) + `for`. A (`for: 1m`) mất 89 s; B và C (`for: 2m`) mất ~150 s; D nhanh nhất (41 s) vì `up == 0` đúng ngay ở lần scrape hỏng đầu tiên và `for` chỉ 30 s. `for` chống báo động giả nhưng cộng thẳng vào thời gian phát hiện.
- **C:** giá token của `mock-llm-large` đắt ×16.7, nhưng cost/giờ đo được chỉ ×8 (≈$2 → $16.4). Lý do: (1) `rate(...[5m])` mới chứa ~2.5 phút dữ liệu model lớn lúc mình đổi lại; (2) latency tăng 2.5 lần nên user gửi ít request hơn (18 → 16 req/s). Alert cost luôn trễ theo cửa sổ 5 phút.
- **D:** trên máy này, container bị pause trả `Connection refused` ngay chứ không treo, nên Nginx `proxy_next_upstream error` chuyển request sang instance còn lại. Sau ~15 giây đầu, Nginx không ghi thêm lỗi 502/504 nào; người dùng gần như không bị ảnh hưởng. Error rate của agent vẫn xanh (0.13%) vì request không bao giờ tới agent bị pause. Đúng như README: metric chỉ phản ánh những gì *chính service đó* nhìn thấy.
- **Sự cố do chính việc đổi cấu hình:** Nginx log cho thấy **~220–250 lỗi 502 (`no live upstreams`) mỗi lần chạy `docker compose up -d agent`** (10:23, 10:24, 10:27, 10:30, 10:31, 10:34). Compose tạo lại *cả hai* container cùng lúc với IP mới; Nginx cache DNS 10 s (`resolver ... valid=10s`) nên trong ~10 s nó gửi vào IP cũ đã chết. Dashboard của agent **không thấy** các lỗi này; chỉ Locust và Nginx thấy. Production cần rolling update (thay từng instance, chờ `/ready`) và giám sát ở tầng load balancer hoặc synthetic check.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Đây là sự cố **chất lượng** chứ không phải hạ tầng: request vẫn 200 và còn nhanh hơn, nhưng accuracy tụt từ 100% xuống 4.3% (đã đo ở mục 3). Cách phát hiện:
1. **Chặn trước khi deploy:** quality gate trong CI (`run_eval.py --min-accuracy 0.85 --baseline ...`, job `.github/workflows/eval.yml`). v1 cho exit code 1 nên không merge/deploy được. Đây là lớp chính.
2. **Canary + online eval:** đưa bản mới cho 5–10% traffic, chạy eval tự động (keyword / LLM-as-judge) trên một mẫu câu hỏi thật, so với bản cũ rồi mới tăng traffic.
3. **Metric hành vi đại diện cho chất lượng:** alert khi retrieval hit ratio giảm hoặc metric retrieval biến mất (`absent(rate(agent_retrieval_total[5m]))`), khi tokens/request thay đổi đột ngột, hoặc khi label `version` trong `agent_info` đổi mà không có deploy được duyệt. Panel *Retrieval hit ratio* trên ảnh vẫn hiện 100% suốt lúc chạy v1 (v1 không ghi metric này, panel chỉ vẽ theo dữ liệu của v2), tức là panel không cảnh báo được gì.
4. **Tín hiệu từ người dùng:** tỉ lệ thumbs-down / câu hỏi lặp lại / phàn nàn, đặt thành SLO chất lượng.

### 4.3 Debug bằng trace

Trace chậm nhất trong 15 phút (tìm qua Jaeger API v3, `query.duration_min=1s`; Jaeger có 475 trace `POST /ask` > 1 s):

- `trace_id`: `7ac7e7a35a39c3951cc1aec4d6edcaea` (10:20:24 UTC, trong lúc benchmark 100 user / 3 instance)
- Tổng thời gian: **1900.7 ms**
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small` (1679.9 ms, 88.4%)**

```text
POST /ask                      1900.7 ms (100.0%)
└── agent.run                  1700.2 ms ( 89.5%)  agent.version=v2
    ├── agent.retrieve_context   20.3 ms (  1.1%)  retrieval.documents=1
    └── chat mock-llm-small    1679.9 ms ( 88.4%)  gen_ai.usage.input_tokens=73, output_tokens=35
```

→ Request chậm vì **LLM upstream** (một request "slow" của mock, +1500 ms), không phải code của agent; retrieval chỉ 20 ms. Có thêm ~200 ms nằm *ngoài* `agent.run` (1900.7 − 1700.2), thấy rõ trên ảnh: `agent.run` bắt đầu muộn ~200 ms so với span gốc. Đó là thời gian request chờ trong event loop / middleware trước khi vào handler, lúc container (giới hạn 0.5 CPU) đang chịu 100 user. Nếu mục tiêu là giảm latency thì phần này mới là phần do agent gây ra.

- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent --no-log-prefix | grep 7ac7e7a35a39c3951cc1aec4d6edcaea`):

```json
{"ts": "2026-11-25T10:20:26.805260+00:00", "level": "INFO", "service": "observable-agent", "instance": "06552b7f5839", "event": "llm_call", "trace_id": "7ac7e7a35a39c3951cc1aec4d6edcaea", "span_id": "881699c6fddb49ee", "request_id": "fe6e855f7a62", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 73, "output_tokens": 35, "cost_usd": 3.2e-05, "llm_ms": 1679.7}
{"ts": "2026-11-25T10:20:26.805668+00:00", "level": "INFO", "service": "observable-agent", "instance": "06552b7f5839", "event": "http_request", "trace_id": "7ac7e7a35a39c3951cc1aec4d6edcaea", "span_id": "881699c6fddb49ee", "request_id": "fe6e855f7a62", "method": "POST", "route": "/ask", "status": 200, "ms": 1900.0}
```

`llm_ms` = 1679.7 trong log khớp span LLM 1679.9 ms; `ms` = 1900.0 khớp span gốc 1900.7 ms.

**Trace lỗi** `799b8afd51832bae41778761bf11e89e` (10:22:28 UTC, 118.9 ms, `POST /ask` → 503): span `chat mock-llm-small` (92.2 ms) có status ERROR, attribute `error.type=upstream_unavailable` và event `exception: utils.mock_llm.LLMError: Upstream LLM error: 503 Service Unavailable (mock)`. Log cùng `trace_id`:

```json
{"ts": "2026-11-25T10:22:28.382028+00:00", "level": "INFO", "service": "observable-agent", "instance": "067a122e1cf1", "event": "llm_error", "trace_id": "799b8afd51832bae41778761bf11e89e", "span_id": "631dc844807a4ce0", "request_id": "4297d168dc2e", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

`docker compose -p k5-day14-observability run --rm locust ... --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_<n>`. Số liệu dòng `POST /ask` trong `benchmark-results/scale_1_stats.csv` và `scale_3_stats.csv`:

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 59.86 | 200 | 260 | 1700 | 66 / 7156 (0.92%) |
| 3 | 59.43 | 190 | 240 | 1700 | 94 / 7106 (1.32%) |

(Trước lần chạy 3 instance đã kiểm tra: Prometheus thấy 3 target `up`, `X-Served-By` xoay vòng qua 3 IP. Failures đều là `503` do mock LLM lỗi ngẫu nhiên ~1%.)

**Giải thích kết quả:** Scale từ 1 lên 3 instance **gần như không đổi gì**: RPS 59.86 → 59.43, p95 260 → 240 ms (−20 ms, trong mức dao động), p99 vẫn 1700 ms.
- **Tải bị giới hạn bởi số user, không phải bởi server.** Locust là closed-loop: mỗi user gửi request, chờ trả lời, rồi nghỉ 0.5–2 s (trung bình 1.25 s). Throughput tối đa ≈ 100 user / (1.25 s + ~0.22 s) ≈ 68 req/s, khớp tổng đo được (65.9 req/s cả `/ask` lẫn `/health`). 1 instance (0.5 CPU) đã phục vụ đủ lượng này với p95 260 ms, chưa bão hoà, nên thêm instance không có gì để cải thiện.
- **Nút thắt của latency là LLM upstream.** p50 ~200 ms gần như toàn bộ là thời gian gọi LLM (120 ms + jitter + token); p99 1700 ms chính là các request "slow" của LLM (+1500 ms, đúng như trace ở 4.3). Thêm instance agent không làm LLM nhanh hơn. Muốn giảm tail latency phải xử lý ở tầng LLM: timeout + retry/hedging, cache câu trả lời, model nhanh hơn, streaming.
- Scale ngang chỉ có ích khi agent bão hoà (CPU/event loop). Ở mục 2.1, một process đã quá tải ở ~100 request đồng thời; muốn thấy lợi ích scale ở đây phải tăng user hoặc bỏ thời gian nghỉ (`wait_time`) để tạo tải open-loop.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Average nói dối, percentile thì không.** Ở `MOCK_SLOW_RATE=0.1`, avg 364 ms vẫn "đạt" 500 ms trong khi p95 1713 ms. SLO và alert phải đặt theo p95/p99. Benchmark cũng phải nói rõ tải là closed-loop hay open-loop, và load generator chạy ở đâu, nếu không thì "scale 3 instance không nhanh hơn" sẽ bị hiểu sai.
  2. **Mỗi tầng chỉ thấy những gì đi qua nó.** Lỗi 502 khi Nginx còn giữ IP cũ lúc tạo lại container, hay instance bị pause, đều không hiện trên error rate của agent. Phải giám sát cả tầng load balancer, synthetic check từ bên ngoài, và dùng trace + log có chung `trace_id` để đi từ một dòng log tới đúng span gây chậm.
  3. **Metric hạ tầng không đo được chất lượng.** v1 trả lời sai 95% câu nhưng dashboard còn xanh hơn v2. Chất lượng phải có hệ đo riêng: golden dataset + quality gate trong CI (exit code chặn merge), so baseline để bắt regression, và so sánh phiên bản bằng *cost / câu đúng* chứ không phải *cost / câu*.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - **Alert theo SLO burn rate** (multi-window, vd 5m + 1h) thay cho ngưỡng tĩnh, để vừa nhanh vừa ít báo giả; alert cost theo **budget thật / cost per request**, không theo $2/giờ cố định (ngưỡng hiện tại bằng mức chi tiêu bình thường).
  - **Metric ở Nginx/LB** (5xx của chính LB, upstream response time, số upstream healthy) và **synthetic probe** gọi `/ask` từ bên ngoài mỗi phút.
  - **Chất lượng:** `absent()` / tỉ lệ retrieval hit ratio, tokens per request, tỉ lệ câu trả lời "fallback chung chung", feedback người dùng; alert khi `agent_info{version}` đổi ngoài quy trình deploy.
  - **Upstream LLM:** tỉ lệ 429/503 theo provider, thời gian tới token đầu (TTFT) khi streaming, độ dài hàng đợi; alert **saturation** (in-flight requests / CPU) trước khi latency vỡ, ví dụ ngưỡng ~40 request đồng thời mỗi instance như đo ở mục 2.1.
  - **Rolling deploy:** cập nhật từng instance, chờ `/ready`, và annotation deploy trên Grafana để biết đỉnh lỗi trùng với lần deploy nào.

---

## Phụ lục — Exercise 7.1: Metrics của agent

Sau 1 request `POST /ask` tới agent v2 (`curl -s http://localhost:8002/metrics | grep -v "^#"`):

| Loại | Ví dụ trong output | Ý nghĩa |
|---|---|---|
| Counter (chỉ tăng) | `llm_tokens_total{model="mock-llm-small",type="input"} 91.0`, `http_requests_total{method="POST",route="/ask",status="200"} 1.0` | Đếm dồn từ lúc process khởi động; xem tốc độ bằng `rate()` |
| Gauge (lên xuống) | `agent_in_flight_requests 0.0`, `agent_info{model="mock-llm-small",version="v2"} 1.0` | Giá trị tức thời |
| Histogram (bucket) | `llm_request_duration_seconds_bucket{le="0.2",model="mock-llm-small"} 1.0` … `_count 1.0`, `_sum 0.177` | Đếm số quan sát ≤ mỗi bucket để tính p95/p99 bằng `histogram_quantile` |

**Dùng `request.url.path` làm label thì sao?** Mỗi URL khác nhau tạo một time series mới cho *mỗi* metric có label `route` (counter + 13 series bucket/sum/count của histogram). Bot quét hàng nghìn URL lạ (`/wp-admin`, `/.env`, `/a1b2c3`…) sẽ sinh hàng nghìn × 15 series không bao giờ mất đi: RAM của agent (registry) và Prometheus tăng không giới hạn, scrape và query chậm dần, có thể làm Prometheus OOM. Đây là **cardinality explosion**, và kẻ tấn công còn có thể chủ động gây ra. Dùng route template thì mọi URL lạ gom vào một nhãn: thử gọi 3 URL lạ cho ra đúng một series `http_requests_total{method="GET",route="unmatched",status="404"} 3.0`.

**Metric mới `agent_question_length_chars`** (đã thêm vào `develop/app.py`): Histogram với bucket `20, 50, 100, 200, 500, 1000, 2000`, observe `len(body.question)` mỗi request. Câu hỏi 40 ký tự ở trên rơi vào bucket `le="50.0"`:

```text
agent_question_length_chars_bucket{le="20.0"} 0.0
agent_question_length_chars_bucket{le="50.0"} 1.0
agent_question_length_chars_count 1.0
agent_question_length_chars_sum 40.0
```

Dùng Histogram chứ không dùng label `length=...` vì độ dài có vô số giá trị (lại đúng vấn đề cardinality ở trên).
