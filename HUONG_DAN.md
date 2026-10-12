# Hướng dẫn sử dụng Trợ lý AI

Trợ lý AI chạy hoàn toàn trên máy bạn: trò chuyện, đọc hiểu tài liệu, và đọc báo giúp bạn.
Trang web: **http://localhost:8080**

> Xem file này đẹp trong VS Code: mở file rồi bấm `Ctrl+Shift+V`.

---

## 1. Bật / tắt

Mở terminal tại thư mục project (`cd ~/PycharmProjects/RAG_LLM`):

| Lệnh | Làm gì |
|---|---|
| `make dev` | Bật toàn bộ hệ thống và mở trình duyệt |
| `make dev-down` | Tắt toàn bộ |
| `make dev-restart` | Khởi động lại phần API (sau khi đổi `.env`) |
| `make dev-status` | Xem model có đang chạy trên GPU không, VRAM còn bao nhiêu |
| `make dev-logs` | Xem log trực tiếp (Ctrl+C để thoát) |
| `make cli` | Chat trên terminal thay vì web |

Không cần `conda activate`, `make dev` tự làm.

---

## 2. Khu Chat: làm việc với tài liệu

Cột trái → **＋ Cuộc trò chuyện mới**.

- **Hỏi gì cũng được**, như chat bình thường.
- **Đính kèm tài liệu**: bấm 📎 hoặc kéo thả file vào khung chat (PDF, Word, Excel, TXT, MD).
  - Thẻ file hiện ngay trong cuộc trò chuyện; gửi câu hỏi lúc file đang tải thì câu hỏi tự chờ.
  - File dài: AI **đọc hết toàn bộ** trước rồi mới trả lời (thẻ file hiện "đang đọc 3/12 phần").
  - Nhiều file trong một cuộc trò chuyện: nói **"file này"** = file gửi gần nhất; nêu tên file
    (vd "file BOM...") = đúng file đó; không nói rõ = dùng tất cả.
  - Bấm × trên thẻ file để gỡ file khỏi cuộc trò chuyện.
- **📚 Thư viện tài liệu**: xem mọi file đã tải, tick để đính kèm lại vào cuộc đang mở, 🗑 để xoá.
- Không đính kèm gì: AI tự tìm trong thư viện khi câu hỏi liên quan.

Mẹo hỏi tài liệu dài:
- "Tóm tắt tài liệu này", "ý chính là gì" → AI dùng ghi chú của **mọi phần**.
- "Liệt kê **tất cả** ..." → AI **đọc lại toàn bộ** nguyên văn (chậm 1-3 phút, có tiến độ).
- Hỏi cụ thể ("chương 3 nói gì về X") → nhanh nhất.

---

## 3. Khu 📰 Tin tức: đọc báo

Cột trái → **📰 Tin tức**. Khu này **tách riêng** với khu chat: chat tài liệu không bao giờ dùng
bài báo, và ngược lại.

- **Ô thời tiết** Hà Nội (dự báo thật từ Open-Meteo). Bấm vào để hỏi chi tiết.
- **Danh sách tin** mới nhất, lọc theo chuyên mục (Thời sự, Thế giới, Kinh doanh, ..., Thời tiết,
  Chính trị). Mỗi tin có tóm tắt AI, tên báo, giờ đăng; bấm tiêu đề để mở bài gốc.
  - **"📰 3 báo đưa tin ▾"**: cùng một sự kiện được nhiều báo viết, gộp làm một; bấm để xem link
    từng báo.
- **↻**: lấy tin ngay (bình thường tự lấy 30 phút/lần). **＋**: cuộc hỏi đáp tin tức mới.
- **Ô hỏi đáp** bên phải:
  - "Hôm nay có tin gì?" → bản tin theo chuyên mục, sự kiện nhiều báo đưa được ưu tiên.
  - "Tin chính trị hôm nay", "Tin công nghệ hôm nay" → bản tin một chuyên mục.
  - "Chiều nay Hà Nội có mưa không?" → trả lời bằng số liệu dự báo thật.
  - Hỏi một sự kiện cụ thể → AI tìm trong các bài đã lấy về.
  - **💬 Hỏi về bài này / sự kiện này** → hỏi sâu; với sự kiện nhiều báo đưa có thể hỏi
    "các báo đưa tin vụ này khác nhau thế nào?".
- Bấm **số trích dẫn** trong câu trả lời để mở bài gốc.

---

## 4. Đọc câu trả lời

- **Chấm số nhỏ [1] [2]**: nguồn của thông tin. Rê chuột xem tên nguồn; với tin tức, bấm để mở bài.
- **⚠ cảnh báo vàng**: số liệu AI đưa ra không thấy trong nguồn → nên kiểm tra lại.
- **ⓘ chi tiết**: AI đã dùng nguồn nào, chế độ đọc gì, mất bao lâu. Gửi kèm phần này khi báo lỗi.

---

## 5. Cập nhật phiên bản

Claude gửi file `RAG_LLM_vX.zip` (chỉ chứa file thay đổi). Tải về `~/Downloads` rồi:

```bash
make update      # sao lưu file cũ, ghi đè, cài thư viện mới nếu có, chạy test, khởi động lại
make rollback    # quay lại bản trước nếu bản mới có vấn đề
```

---

## 6. Cấu hình (`.env`)

Sửa file `.env` rồi `make dev-restart`. Mẫu đầy đủ trong `.env.example`.

| Biến | Ý nghĩa |
|---|---|
| `LLM_MODEL` | Model trả lời (vd `qwen2.5:7b`) |
| `EMBED_MODEL` | Model tìm kiếm (`bge-m3-cpu` trên máy GPU nhỏ, `bge-m3` khi GPU đủ lớn) |
| `NEWS_FEEDS` | Danh sách chuyên mục và link RSS các báo |
| `NEWS_TOPICS` | Chuyên mục theo từ khoá (Thời tiết, Chính trị) |
| `NEWS_INTERVAL_MIN`, `NEWS_KEEP_DAYS` | Bao lâu lấy tin một lần, giữ tin bao nhiêu ngày |
| `WEATHER_LOCATION`, `WEATHER_LAT`, `WEATHER_LON` | Nơi xem thời tiết |
| `QDRANT_URL` | Để trống = kho vector nhúng; `http://localhost:6333` = Qdrant server |

### Thêm một báo mới

1. Tìm link RSS của báo (thường ở trang `.../rss` cuối trang chủ).
2. Thử trước: `make news-check URL=https://.../rss/thoi-su.rss`
   → phải thấy ✓ và "Nội dung: ... ký tự" đủ dài.
3. Thêm link vào chuyên mục tương ứng trong `NEWS_FEEDS` (`.env`), rồi `make dev-restart`.

---

## 7. Khi có vấn đề

| Hiện tượng | Làm gì |
|---|---|
| Web không mở / báo lỗi kết nối | `make dev-status`, rồi `make dev-restart` |
| Trả lời rất chậm | `make dev-status`: cột PROCESSOR phải là `100% GPU` |
| Tin tức không cập nhật | `make news-check` để xem nguồn nào lỗi |
| Lỗi khác | `tail -n 300 .run/api.log > ~/api_log.txt` rồi gửi file đó cho Claude |

---

## 8. Chuyển kho vector sang Qdrant server (khi dữ liệu lớn / deploy)

Khi có nhiều tin tức (vài chục nghìn vector), Qdrant server (Docker) nhanh hơn kho nhúng. Chuyển
một lần, dữ liệu được chép sang, không phải nạp lại:

```bash
make qdrant-switch
```

Từ đó `make dev` / `make dev-down` tự bật / tắt Qdrant server. Thư mục `data/qdrant` cũ giữ
nguyên làm bản sao lưu.

---

## 9. Triển khai lên server GPU (Docker)

Trên server mọi thứ chạy bằng Docker: web + API, Qdrant, Ollama (LLM và model tìm kiếm đều
trên GPU). Không cần conda. Cần: Docker, Docker Compose, driver NVIDIA, nvidia-container-toolkit,
GPU còn trống khoảng 9-10 GB (qwen2.5:7b + bge-m3).

**Lần đầu**

```bash
# 1. Trên máy dev: chép code (kèm .env) lên server
rsync -av --exclude data --exclude .run --exclude __pycache__ \
  ~/PycharmProjects/RAG_LLM/ thuan@<server>:~/RAG_LLM/

# 2. (Tuỳ chọn) chép cả dữ liệu: tài liệu, lịch sử chat, tin tức. Tắt hệ thống trước.
make dev-down
rsync -av --exclude qdrant ~/PycharmProjects/RAG_LLM/data/ thuan@<server>:~/RAG_LLM/data/

# 3. Trên server
cd ~/RAG_LLM
make server-check    # kiểm tra Docker, GPU; phải thấy toàn ✓
make server-up       # lần đầu tự tải model (vài phút), tự dựng lại kho vector từ dữ liệu đã chép
```

Kho vector không cần chép: lúc khởi động, hệ thống thấy dữ liệu chưa có vector (hoặc đổi model
embedding từ `bge-m3-cpu` sang `bge-m3`) thì tự tính lại trên GPU.

**Mở web từ máy của bạn** (mặc định server chỉ mở cổng cho chính nó, an toàn vì web chưa có
đăng nhập):

```bash
ssh -L 8080:localhost:8080 thuan@<server>     # giữ terminal này mở
# rồi mở http://localhost:8080 trên máy của bạn
```

Muốn cả mạng nội bộ vào thẳng `http://<server>:8080`: đặt `API_BIND=0.0.0.0` trong `.env` trên
server rồi `make server-up`.

**Hằng ngày**

| Lệnh (trên server) | Làm gì |
|---|---|
| `make server-up` | Bật, hoặc áp code mới sau khi rsync lại |
| `make server-down` | Tắt (dữ liệu giữ nguyên) |
| `make server-status` | Container nào đang chạy, model nào trên GPU, VRAM |
| `make server-logs` | Xem log API |

Server khởi động lại thì hệ thống tự bật lại. Đổi model: sửa `server.env` rồi `make server-up`
(model mới tự tải về). `server.env` là cấu hình riêng của server, ghi đè `.env`.
