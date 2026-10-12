# ADR 0001: Gọi LLM qua API chuẩn OpenAI

Trạng thái: chấp nhận

Bối cảnh
Chưa chốt model và runtime (Ollama / llama.cpp / vLLM). Phát triển trên máy dev
(GPU nhỏ), sau đó deploy lên server (GPU lớn, nhiều người dùng đồng thời).

Quyết định
Backend chỉ nói chuyện với LLM qua API chuẩn OpenAI (/v1/chat/completions),
bọc trong services/llm.py. Địa chỉ, tên model, timeout lấy từ biến môi trường.

Hệ quả
- Đổi runtime hoặc model chỉ cần sửa .env, không sửa code.
- Dev dùng Ollama, server có thể dùng vLLM để chịu tải đồng thời.
- Không dùng tính năng riêng của một runtime; nếu cần sẽ ghi ADR mới.
