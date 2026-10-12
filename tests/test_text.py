from assistant.ingestion.text import fold, looks_like_heading, normalize_text


def test_normalize_text_cleans_invisible_and_spaces():
    raw = "Xin\u00a0chào\u200b  bạn\r\n\r\n\r\n\r\nHết  "
    assert normalize_text(raw) == "Xin chào bạn\n\nHết"


def test_normalize_text_composes_unicode():
    decomposed = "Nghỉ phép"  # chữ ỉ dạng tổ hợp
    assert normalize_text(decomposed) == "Nghỉ phép"


def test_fold_removes_diacritics_and_d_bar():
    assert fold("Điều 5: Nghỉ phép năm") == "dieu 5: nghi phep nam"


def test_headings_detected():
    for line in [
        "Điều 5. Nghỉ phép năm",
        "Chương II",
        "CHƯƠNG 3: QUY ĐỊNH CHUNG",
        "1.2 Phạm vi áp dụng",
        "II. QUY TRÌNH THANH TOÁN",
        "QUY ĐỊNH NHÂN SỰ",
        "3. Giờ làm việc",
        "Dieu 7 Thu viec",
    ]:
        assert looks_like_heading(line), line


def test_non_headings_rejected():
    for line in [
        "12 ngày làm việc mỗi năm",
        "1.5 triệu đồng mỗi tháng",
        "Nhân viên chính thức được nghỉ phép năm 12 ngày.",
        "2. Nộp đơn trước 3 ngày.",
        "",
        "Trang 3",
        "12 tháng 5 năm 2026",
        "a" * 200,
    ]:
        assert not looks_like_heading(line), line
