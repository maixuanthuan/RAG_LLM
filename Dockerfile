FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . tzdata

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=4)"
# 1 worker: việc nền (đọc tài liệu dài, lấy tin) chạy trong tiến trình API, không được nhân đôi
CMD ["uvicorn", "assistant.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
