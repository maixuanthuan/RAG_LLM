# RAG_LLM: trợ lý AI chạy local

Chatbot chạy hoàn toàn trên máy của bạn: LLM local + RAG để hỏi đáp tài liệu, kèm một khu đọc
báo riêng. Không gọi API LLM bên ngoài; mạng chỉ dùng để lấy tin tức và thời tiết.

> Hướng dẫn dùng hằng ngày (tiếng Việt, cho người dùng): **[HUONG_DAN.md](HUONG_DAN.md)**.
> File này thiên về kỹ thuật.

## Tính năng

**Khu Chat: làm việc với tài liệu**
- Giao diện web kiểu ChatGPT: lịch sử hội thoại, kéo thả file (PDF, Word, Excel, TXT, MD) vào
  cuộc trò chuyện, nhiều file ở nhiều thời điểm ("file này" = file gửi gần nhất).
- Trả lời bám nguồn: trích dẫn `[n]` tới file / trang / mục, cảnh báo số liệu không có trong nguồn,
  câu hỏi ngoài tài liệu vẫn trả lời bình thường.
- Tài liệu dài: AI **đọc trước toàn bộ** theo từng phần (ghi chú + tổng quan) rồi mới trả lời;
  câu hỏi "liệt kê tất cả" thì đọc lại nguyên văn từng phần.
- Tìm kiếm hybrid: vector (bge-m3, Qdrant) + từ khoá (SQLite FTS5 BM25), hợp nhất RRF.

**Khu Tin tức: đọc báo (tách hẳn khỏi khu Chat)**
- Lấy tin từ RSS các báo chính thống (VnExpress, Tuổi Trẻ, Thanh Niên, Dân trí), lấy toàn văn,
  AI tóm tắt từng bài, giữ link gốc. Tôn trọng robots.txt, giãn cách request.
- Gộp cùng một sự kiện từ nhiều báo; chuyên mục theo từ khoá (Thời tiết, Chính trị).
- "Hôm nay có tin gì?" ra bản tin theo chuyên mục; hỏi sâu từng bài / sự kiện.
- Thời tiết bằng số liệu dự báo thật (Open-Meteo).

## Kiến trúc

```
Trình duyệt ──► FastAPI (API + web 1 file HTML, SSE streaming)
                 ├─ LLM + embedding: Ollama (API chuẩn OpenAI, đổi sang vLLM chỉ cần sửa .env)
                 ├─ Vector: Qdrant (nhúng hoặc server)
                 ├─ SQLite: tài liệu, đoạn văn + BM25, hội thoại, bài báo, ghi chú đọc trước
                 └─ Việc nền: đọc trước tài liệu dài · lấy tin + tóm tắt (nhường máy khi đang chat)
```

Model mặc định: `qwen2.5:7b` (trả lời), `bge-m3` (embedding, 1024 chiều).

## Chạy trên máy dev

Cần: Python 3.10+, conda, [Ollama](https://ollama.com), GPU NVIDIA (khuyến nghị).

```bash
conda create -n rag_llm python=3.11 -y && conda activate rag_llm
make install          # cài thư viện, tạo .env từ .env.example
make dev              # bật Ollama + API nền + mở http://localhost:8080
```

Lần đầu cần kéo model: `make ollama-pull` (đọc `LLM_MODEL` trong `.env`) và
`LLM_MODEL=bge-m3 make ollama-pull`.

| Lệnh | Làm gì |
|---|---|
| `make dev` / `make dev-down` | bật / tắt toàn bộ |
| `make dev-restart` | khởi động lại API (sau khi đổi code / `.env`) |
| `make dev-status` | model nào đang trên GPU, VRAM |
| `make dev-logs` | log API (mỗi câu hỏi một dòng: nguồn, độ giống, thời gian) |
| `make cli` | chat trên terminal |
| `make test` / `make lint` | test (không cần GPU, dùng LLM giả) / kiểm tra style |
| `make news-check [URL=...]` | kiểm tra nguồn RSS |

## Triển khai trên server GPU (Docker)

Toàn bộ chạy bằng Docker Compose: API + web, Qdrant, Ollama (LLM và embedding trên GPU).
Cần Docker, driver NVIDIA, nvidia-container-toolkit, khoảng 9-10 GB VRAM trống.

```bash
make server-check     # kiểm tra Docker / GPU
make server-up        # build + bật; lần đầu tự tải model
make server-status    # container, model trên GPU, VRAM
make server-logs
make server-down
```

- Cấu hình: `.env` (dùng chung với máy dev) + `server.env` (ghi đè cho server: model, địa chỉ dịch vụ).
- Chép `data/` từ máy dev sang là có lại tài liệu, hội thoại, tin tức; kho vector tự dựng lại lúc
  khởi động (cũng tự làm khi đổi model embedding).
- Cổng web mặc định chỉ mở trên chính server (`API_BIND=127.0.0.1`); đặt `API_BIND=0.0.0.0`
  để mở cho mạng nội bộ. Web chưa có đăng nhập.

Chi tiết từng bước: mục 9 trong [HUONG_DAN.md](HUONG_DAN.md).

## Cấu hình chính (`.env`)

| Biến | Ý nghĩa |
|---|---|
| `LLM_BASE_URL`, `LLM_MODEL` | LLM server (chuẩn OpenAI) và model trả lời |
| `EMBED_MODEL`, `EMBED_BASE_URL` | model embedding (mặc định dùng chung server LLM) |
| `QDRANT_URL` | trống = Qdrant nhúng trong `data/qdrant`; có = Qdrant server |
| `FOCUS_MAX_CHARS`, `DIGEST_*` | ngưỡng đọc nguyên văn / đọc trước tài liệu dài |
| `MIN_RELEVANCE` | độ giống tối thiểu khi tự tìm trong thư viện |
| `NEWS_*`, `WEATHER_*` | nguồn báo, chu kỳ lấy tin, thời gian giữ tin, nơi xem thời tiết |

Danh sách đầy đủ kèm giải thích: [.env.example](.env.example).

## Cấu trúc

```
src/assistant/
  main.py          khởi tạo app, chạy việc nền, phục vụ web
  core/            cấu hình (.env), logging
  api/routes/      health, chat, documents, conversations, news
  services/        chat (RAG + tin tức + thời tiết), llm, digest (đọc trước), news (worker lấy tin)
  ingestion/       đọc file (txt, md, docx, pdf, xlsx), chuẩn hoá, cắt đoạn, pipeline nạp
  rag/             SQLite + BM25, Qdrant, embedder, retriever hybrid, grounding, dựng lại kho vector
  news/            RSS + lấy toàn văn, gộp sự kiện, bản tin, thời tiết
  web/index.html   giao diện web (1 file, không build)
scripts/           dev.sh, server.sh, update.sh, ollama_local.sh, news.py, ingest.py, ...
tests/             test dùng LLM / embedding giả, không cần GPU
docs/              roadmap, quyết định kỹ thuật (ADR)
```

## API

| Endpoint | Mô tả |
|---|---|
| `GET /health`, `GET /health/llm` | API còn sống / LLM đã sẵn sàng |
| `GET/POST /api/conversations`, `GET/PATCH/DELETE /api/conversations/{id}` | hội thoại |
| `POST /api/conversations/{id}/messages` | gửi câu hỏi, trả lời dạng SSE (lưu lịch sử) |
| `POST /api/conversations/{id}/attachments`, `DELETE .../attachments/{doc_id}` | đính kèm / gỡ tài liệu |
| `GET/POST /api/documents`, `DELETE /api/documents/{id}` | thư viện tài liệu |
| `GET /api/news`, `/api/news/status`, `/api/news/weather`, `POST /api/news/refresh` | khu tin tức |
| `POST /api/chat` | chat không lưu lịch sử (SSE hoặc JSON) |

Tài liệu API tự sinh: `http://localhost:8080/docs`.
