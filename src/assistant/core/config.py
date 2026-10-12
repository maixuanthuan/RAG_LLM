from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Gốc RSS các báo (link từng chuyên mục: kiểm tra bằng `make news-check`)
VNE = "https://vnexpress.net/rss"
TT = "https://tuoitre.vn/rss"
TN = "https://thanhnien.vn/rss"
DT = "https://dantri.com.vn/rss"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    log_level: str = "INFO"

    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "local"
    llm_model: str = "qwen2.5:3b"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 2048
    llm_timeout_s: float = 120.0

    # --- Phase 2: lưu trữ, embedding, truy xuất ---
    sqlite_path: str = "data/assistant.db"
    qdrant_url: str | None = None  # để trống = chạy Qdrant nhúng (lưu ở qdrant_path)
    qdrant_path: str = "data/qdrant"
    qdrant_collection: str = "chunks"

    embed_base_url: str | None = None  # để trống = dùng chung LLM_BASE_URL
    embed_api_key: str | None = None
    embed_model: str = "bge-m3"
    embed_query_prefix: str = ""  # model kiểu E5 cần "query: " / "passage: "
    embed_passage_prefix: str = ""
    embed_batch_size: int = 16

    chunk_size: int = 1200  # ký tự
    chunk_overlap: int = 200
    retrieval_top_k: int = 5
    retrieval_candidates: int = 20  # số ứng viên lấy từ mỗi bộ tìm trước khi hợp nhất
    rrf_k: int = 60

    # --- Phase 3: chat + RAG ---
    uploads_dir: str = "data/uploads"  # file upload qua API, thư mục con = nhóm
    max_upload_mb: int = 50
    focus_max_chars: int = 12000  # tài liệu ghim ngắn hơn mức này -> đưa nguyên văn vào prompt
    history_max_messages: int = 8  # số tin nhắn gần nhất gửi kèm cho LLM
    grounded_temperature: float = 0.1  # nhiệt độ khi trả lời có nguồn (ít sáng tác hơn)
    rewrite_max_tokens: int = 128

    # --- Đọc trước tài liệu dài (chạy nền sau khi nạp) ---
    digest_enabled: bool = True
    digest_part_chars: int = 6000  # mỗi lần AI đọc ~6000 ký tự nguyên văn (vừa context 8k)
    digest_note_tokens: int = 700  # độ dài tối đa ghi chú cho mỗi phần
    scan_extract_tokens: int = 500  # độ dài tối đa phần trích khi quét toàn bộ để trả lời
    digest_wait_timeout_s: float = 1800  # câu hỏi về tài liệu dài chờ AI đọc xong tối đa bấy lâu
    digest_wait_poll_s: float = 2.0
    neighbor_chunks: int = 1  # tài liệu đính kèm dài: lấy thêm N đoạn trước/sau đoạn tìm được

    # --- Tin tức (khu Tin tức, tách riêng khỏi chat tài liệu) ---
    news_enabled: bool = True
    news_dir: str = "data/news"  # nội dung bài báo lưu thành file .md (nạp như tài liệu)
    news_interval_min: float = 30  # bao lâu lấy tin một lần
    news_keep_days: float = 7  # bài cũ hơn thì xoá
    news_max_per_feed: int = 15  # mỗi lần lấy tối đa bao nhiêu bài mới nhất của một feed
    news_request_delay_s: float = 1.0  # nghỉ giữa các request tới báo (lịch sự, tránh bị chặn)
    news_digest_hours: float = 24  # "hôm nay có tin gì" = các bài trong bấy nhiêu giờ qua
    news_summary_tokens: int = 220
    # chuyên mục -> tên hiển thị + RSS. Đổi trong .env dạng JSON:
    # NEWS_FEEDS='{"thoi-su": {"label": "Thời sự", "urls": ["https://.../thoi-su.rss"]}}'
    news_feeds: dict[str, dict] = {
        "thoi-su": {"label": "Thời sự", "urls": [
            f"{VNE}/thoi-su.rss", f"{TT}/thoi-su.rss", f"{TN}/thoi-su.rss", f"{DT}/thoi-su.rss",
        ]},
        "the-gioi": {"label": "Thế giới", "urls": [
            f"{VNE}/the-gioi.rss", f"{TT}/the-gioi.rss", f"{TN}/the-gioi.rss", f"{DT}/the-gioi.rss",
        ]},
        "kinh-doanh": {"label": "Kinh doanh", "urls": [
            f"{VNE}/kinh-doanh.rss", f"{TT}/kinh-doanh.rss", f"{TN}/kinh-te.rss",
            f"{DT}/kinh-doanh.rss",
        ]},
        "phap-luat": {"label": "Pháp luật", "urls": [
            f"{VNE}/phap-luat.rss", f"{TT}/phap-luat.rss", f"{DT}/phap-luat.rss",
        ]},
        "giao-duc": {"label": "Giáo dục", "urls": [
            f"{VNE}/giao-duc.rss", f"{TT}/giao-duc.rss", f"{TN}/giao-duc.rss", f"{DT}/giao-duc.rss",
        ]},
        "suc-khoe": {"label": "Sức khỏe", "urls": [
            f"{VNE}/suc-khoe.rss", f"{TT}/suc-khoe.rss", f"{TN}/suc-khoe.rss", f"{DT}/suc-khoe.rss",
        ]},
        "doi-song": {"label": "Đời sống", "urls": [
            f"{VNE}/doi-song.rss", f"{DT}/doi-song.rss",
        ]},
        "giao-thong": {"label": "Giao thông", "urls": [
            f"{VNE}/oto-xe-may.rss", f"{TT}/xe.rss", f"{TN}/xe.rss", f"{DT}/o-to-xe-may.rss",
        ]},
        "cong-nghe": {"label": "Công nghệ", "urls": [
            f"{VNE}/so-hoa.rss", f"{TT}/nhip-song-so.rss", f"{TN}/cong-nghe.rss",
            f"{DT}/cong-nghe.rss",
        ]},
        "khoa-hoc": {"label": "Khoa học", "urls": [f"{VNE}/khoa-hoc.rss", f"{TT}/khoa-hoc.rss"]},
    }  # fmt: skip

    # Chuyên mục theo từ khoá: bài ở bất kỳ chuyên mục nào có từ khoá (so có dấu, nguyên từ) trong
    # tiêu đề/sapo thì được gắn thêm nhãn này. Đổi trong .env dạng JSON như NEWS_FEEDS.
    news_topics: dict[str, dict] = {
        "thoi-tiet": {"label": "Thời tiết", "keywords": [
            "thời tiết", "bão", "áp thấp nhiệt đới", "mưa lớn", "mưa to", "mưa lũ", "lũ lụt",
            "lũ quét", "ngập", "ngập lụt", "nắng nóng", "rét đậm", "rét hại", "không khí lạnh",
            "triều cường", "sạt lở", "dông", "giông", "lốc xoáy", "mưa đá", "nhiệt độ",
        ]},
        "chinh-tri": {"label": "Chính trị", "keywords": [
            "Quốc hội", "đại biểu Quốc hội", "Bộ Chính trị", "Ban Bí thư", "Tổng Bí thư",
            "Trung ương Đảng", "hội nghị Trung ương", "Chủ tịch nước", "Thủ tướng", "Phó thủ tướng",
            "Chính phủ", "nghị quyết", "bầu cử", "ngoại giao", "tổng thống", "đại sứ",
            "Liên Hợp Quốc", "hội nghị thượng đỉnh",
        ]},
    }  # fmt: skip
    # Thời tiết (Open-Meteo, miễn phí, không cần API key)
    weather_enabled: bool = True
    weather_location: str = "Hà Nội"
    weather_lat: float = 21.0285
    weather_lon: float = 105.8542
    weather_cache_min: float = 30
    news_event_threshold: float = 0.82  # độ giống tiêu đề+sapo để coi là cùng một sự kiện
    news_event_window_h: float = 48  # chỉ gom các bài đăng cách nhau tối đa bấy nhiêu giờ
    # Việc nền của tin tức tạm dừng khi người dùng đang chat / tải file và bấy nhiêu giây sau đó
    news_yield_s: float = 10.0

    # Ngưỡng độ giống (cosine) tối thiểu khi tìm trên toàn kho: đoạn thấp hơn bị bỏ, giống
    # ngưỡng confidence của detector. Câu chào hỏi/ngoài lề sẽ không kéo theo nguồn nào.
    min_relevance: float = 0.5
    timezone: str = "Asia/Ho_Chi_Minh"

    system_prompt: str = (
        "Bạn là trợ lý AI cá nhân, nói chuyện tự nhiên bằng tiếng Việt, thân thiện, đi thẳng vào "
        "vấn đề. Bạn có thể: trò chuyện và giải đáp thắc mắc thông thường; đọc, tóm tắt, "
        "phân tích, so sánh và trả lời câu hỏi về tài liệu người dùng đưa vào "
        "(PDF, Word, Excel, văn bản). "
        "Bạn không truy cập được internet hay dữ liệu thời gian thực (thời tiết, giá cả, tin tức) "
        "ngoài những gì được cung cấp trong cuộc trò chuyện. "
        "Trả lời đúng trọng tâm câu hỏi, độ dài vừa với câu hỏi: câu hỏi xác nhận (có/không, "
        "đọc được không) thì trả lời ngắn kèm vài ý chính; khi tóm tắt thì tóm ý bằng lời của "
        "bạn, không chép lại nguyên văn tài liệu."
    )

    @property
    def embed_url(self) -> str:
        return self.embed_base_url or self.llm_base_url

    @property
    def embed_key(self) -> str:
        return self.embed_api_key or self.llm_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
