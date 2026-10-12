# Dùng Python của môi trường đang kích hoạt (conda env hoặc venv).
# Không bao giờ cài vào Python hệ thống hoặc conda base.
PY ?= python

.PHONY: check-env install run test lint fmt chat bench up down update news-check news-fetch qdrant-up qdrant-down qdrant-switch dev cli dev-up dev-down dev-restart dev-status dev-logs ollama-up ollama-down ollama-pull ollama-check ollama-status server-check server-up server-down server-status server-logs sample-docs ingest search eval kb-stats

check-env:
	@$(PY) -c "import os,sys; ok = sys.prefix != sys.base_prefix or os.environ.get('CONDA_DEFAULT_ENV', 'base') != 'base'; sys.exit(0 if ok else 1)" || { echo "LOI: chua kich hoat env rieng (conda env hoac venv). Khong cai vao Python he thong hoac conda base."; exit 1; }

install: check-env
	$(PY) -m pip install -U pip
	$(PY) -m pip install -e ".[dev]"
	@test -f .env || cp .env.example .env

run: check-env
	$(PY) -m uvicorn assistant.main:app --reload --host 127.0.0.1 --port 8080

test: check-env
	$(PY) -m pytest -q

lint: check-env
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

fmt: check-env
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .

chat: check-env
	$(PY) scripts/chat_cli.py

bench: check-env
	$(PY) scripts/bench_llm.py --concurrency 1 --rounds 3

up:
	docker compose up -d --build

down:
	docker compose down

# Tin tức: kiểm tra nguồn RSS / lấy tin ngay (API phải đang chạy)
news-check: check-env
	$(PY) scripts/news.py check $(if $(URL),--url "$(URL)")

news-fetch: check-env
	$(PY) scripts/news.py fetch

# Qdrant server (Docker): kho vector cho dữ liệu lớn / deploy thật
qdrant-up:
	bash scripts/dev.sh qdrant-up

qdrant-down:
	bash scripts/dev.sh qdrant-down

# Chuyển một lần từ Qdrant nhúng sang server: bật Qdrant (kiểm tra Docker trước khi tắt gì),
# tắt hệ thống, chép vector, bật lại. Lỗi ở bước nào thì hệ thống vẫn bật lại như cũ.
qdrant-switch: check-env
	bash scripts/dev.sh qdrant-up
	bash scripts/dev.sh down
	$(PY) scripts/qdrant_migrate.py || { bash scripts/dev.sh web; exit 1; }
	bash scripts/dev.sh web

# Server GPU: chạy toàn bộ bằng Docker (docker-compose.server.yml), không cần conda
server-check:
	bash scripts/server.sh check

server-up:
	bash scripts/server.sh up

server-down:
	bash scripts/server.sh down

server-status:
	bash scripts/server.sh status

server-logs:
	bash scripts/server.sh logs

# Áp bản cập nhật code (zip chỉ chứa file thay đổi). Quay lại bản trước: git restore .
update:
	bash scripts/update.sh update $(ZIP)

# Chạy cả hệ thống bằng 1 lệnh (tự kích hoạt conda env rag_llm): Ollama + API nền + mở web
dev:
	bash scripts/dev.sh web

# Như trên nhưng chat trên terminal
cli:
	bash scripts/dev.sh chat

dev-up:
	bash scripts/dev.sh up

dev-down:
	bash scripts/dev.sh down

dev-restart:
	bash scripts/dev.sh restart

dev-status:
	bash scripts/dev.sh status

dev-logs:
	bash scripts/dev.sh logs

# Ollama riêng của project (cổng 11435, ép dùng GPU qua Vulkan), không sudo
ollama-up:
	bash scripts/ollama_local.sh start

ollama-pull:
	bash scripts/ollama_local.sh pull

ollama-check:
	bash scripts/ollama_local.sh check

ollama-status:
	bash scripts/ollama_local.sh status

ollama-down:
	bash scripts/ollama_local.sh stop

# ---- Phase 2: nạp tài liệu và truy xuất ----
DOCS ?= data/sample_docs
GOLDEN ?= eval/golden_sample.jsonl

sample-docs: check-env
	$(PY) scripts/make_sample_docs.py

ingest: check-env
	$(PY) scripts/ingest.py $(DOCS) $(ARGS)

kb-stats: check-env
	$(PY) scripts/ingest.py --stats

search: check-env
	$(PY) scripts/search.py "$(Q)" $(ARGS)

eval: check-env
	$(PY) scripts/eval_retrieval.py $(GOLDEN)
