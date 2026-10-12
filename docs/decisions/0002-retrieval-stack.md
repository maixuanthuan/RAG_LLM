# ADR 0002: Kiến trúc lưu trữ và truy xuất (Phase 2)

Trạng thái: chấp nhận tạm thời, xác nhận lại sau khi đánh giá trên tài liệu thật

Quyết định
- Metadata, nội dung chunk và chỉ mục từ khoá: SQLite (FTS5, xếp hạng BM25). Từ khoá được bỏ dấu
  khi lập chỉ mục và khi truy vấn để khớp cả câu hỏi gõ không dấu.
- Vector: Qdrant. Mặc định chạy nhúng (thư mục data/qdrant); chạy server khi cần nhiều tiến trình
  cùng truy cập (chế độ nhúng khoá thư mục, API và script nạp không mở cùng lúc được).
- Embedding: gọi qua API chuẩn OpenAI (/v1/embeddings), ứng viên đầu tiên là bge-m3 (đa ngôn ngữ).
  Có thể đổi model bằng cấu hình; model kiểu E5 dùng EMBED_QUERY_PREFIX / EMBED_PASSAGE_PREFIX.
- Hợp nhất: vector + từ khoá bằng Reciprocal Rank Fusion (không cần chuẩn hoá điểm).
- Chunk theo cấu trúc: không vượt ranh giới mục, giữ trang và đường dẫn mục để trích dẫn.
  Văn bản đưa vào embedding có thêm tên tài liệu và mục làm tiêu đề ngữ cảnh.
- Quyền truy cập: mọi chế độ tìm kiếm nhận danh sách phòng ban được phép và lọc ngay tại tầng
  truy xuất (Qdrant filter và điều kiện SQL), không chỉ ở giao diện.
- Reranker: chưa thêm. Chỉ thêm nếu scripts/eval_retrieval.py trên tài liệu thật cho thấy cần.

Hạn chế đã biết
- PDF bản scan chưa có OCR (script cảnh báo theo từng trang); bảng trong PDF mất cấu trúc.
- DOCX không có số trang cố định nên trích dẫn theo mục, không theo trang.
- Tiêu đề trong PDF/TXT nhận diện bằng heuristic (Điều, Chương, đánh số, dòng in hoa).
- Model embedding chạy trên Ollama dùng chung VRAM với LLM; xem docs/roadmap.md cho Phase 3.
