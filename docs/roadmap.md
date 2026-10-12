# Roadmap

Phase 1  Local LLM                      xong
  [x] API chat streaming, health check, test; Ollama riêng trên máy dev
  [x] Gọi LLM qua API chuẩn OpenAI (đổi runtime / model chỉ sửa .env)
Phase 2  Nạp tài liệu + RAG             xong
  [x] Đọc TXT/MD/DOCX/PDF/XLSX, cắt đoạn theo cấu trúc, metadata trang / mục
  [x] Tìm hybrid vector (bge-m3, Qdrant) + từ khoá (SQLite FTS5), RRF; công cụ `make eval`
  [ ] Đo trên bộ tài liệu thật + câu hỏi có đáp án; quyết định có cần reranker
Phase 3  Chat + RAG                     xong
  [x] Viết lại câu hỏi nối tiếp, trích dẫn, cảnh báo số liệu không có trong nguồn
  [x] Tài liệu dài: đọc trước toàn bộ, phân loại câu hỏi, quét toàn văn khi cần
  [x] Nhiều file trong một cuộc trò chuyện, "file này" = file mới nhất
Phase 4  Web + Tin tức                  xong
  [x] Web kiểu ChatGPT: lịch sử, thẻ file, thư viện, chi tiết nguồn
  [x] Khu Tin tức tách riêng: RSS 4 báo, toàn văn, tóm tắt, gộp sự kiện, bản tin, thời tiết
Phase 5  Triển khai GPU                 đã chuẩn bị
  [x] Docker Compose đầy đủ (API, Qdrant, Ollama GPU), tự dựng lại kho vector
  [ ] Chạy thật trên server GPU, đo tốc độ
Phase 6  Mở rộng (tuỳ chọn)
  [ ] Đăng nhập (nếu cho người khác dùng)
  [ ] Bản tin sáng tự gửi, theo dõi chủ đề
  [ ] OCR cho PDF scan, giữ cấu trúc bảng
