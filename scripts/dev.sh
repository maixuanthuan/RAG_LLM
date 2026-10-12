#!/usr/bin/env bash
# Chạy cả hệ thống bằng 1 lệnh: Ollama riêng (GPU) + API chạy nền + chat trên terminal.
# Tự kích hoạt conda env (mặc định rag_llm), không cần mở nhiều terminal.
#
#   bash scripts/dev.sh web       bật Ollama + API nếu chưa bật, mở trình duyệt (make dev)
#   bash scripts/dev.sh chat      như trên nhưng chat trên terminal            (make cli)
#   bash scripts/dev.sh up        chỉ bật Ollama + API chạy nền                (make dev-up)
#   bash scripts/dev.sh restart   khởi động lại API (sau khi cập nhật code)    (make dev-restart)
#   bash scripts/dev.sh status    API, model nào đang nằm trên GPU, VRAM       (make dev-status)
#   bash scripts/dev.sh logs      xem log API                                  (make dev-logs)
#   bash scripts/dev.sh down      tắt API + Ollama (+ Qdrant server nếu dùng)   (make dev-down)
#   bash scripts/dev.sh qdrant-up bật Qdrant server (Docker)                   (make qdrant-up)
#   bash scripts/dev.sh qdrant-down tắt Qdrant server                          (make qdrant-down)
#
# Biến có thể đổi: RAG_ENV (tên conda env), API_PORT (8080), OLLAMA_CONTEXT_LENGTH (8192).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
RUN_DIR="$ROOT/.run"
API_PID="$RUN_DIR/api.pid"
API_LOG="$RUN_DIR/api.log"
API_PORT="${API_PORT:-8080}"
API_URL="http://localhost:${API_PORT}"
ENV_NAME="${RAG_ENV:-rag_llm}"

activate_env() {
  if [ "${CONDA_DEFAULT_ENV:-}" = "$ENV_NAME" ]; then return 0; fi
  command -v conda >/dev/null 2>&1 || { echo "LOI: khong thay conda trong PATH."; exit 1; }
  set +u  # script activate cua conda dung bien chua khai bao
  # shellcheck disable=SC1091
  source "$(conda info --base)/etc/profile.d/conda.sh"
  conda activate "$ENV_NAME" || { echo "LOI: khong kich hoat duoc env '$ENV_NAME'."; exit 1; }
  set -u
}

qdrant_url() {  # QDRANT_URL trong .env (để trống = Qdrant nhúng, không cần Docker)
  [ -f .env ] && grep -E '^QDRANT_URL=' .env | tail -1 | cut -d= -f2- | tr -d '"' || true
}

compose() {  # docker compose (plugin v2) hoặc docker-compose (bản rời)
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    echo "LOI: chua co Docker Compose. Cai bang:  sudo apt install docker-compose-v2"
    return 1
  fi
}

cmd_qdrant_up() {
  command -v docker >/dev/null 2>&1 || { echo "LOI: chua cai Docker."; exit 1; }
  if ! compose --profile qdrant up -d qdrant; then
    echo "LOI: khong bat duoc Qdrant. Neu bao 'permission denied':"
    echo "  sudo usermod -aG docker \$USER   roi dang xuat / dang nhap lai"
    echo "Xem log: docker ps -a ; hoac thu lai: make qdrant-up"
    exit 1
  fi
  for _ in $(seq 1 30); do
    if curl -s -o /dev/null --max-time 2 "http://localhost:6333/collections"; then
      echo "Qdrant server san sang: http://localhost:6333"
      return 0
    fi
    sleep 1
  done
  echo "LOI: Qdrant chua san sang sau 30 giay. Xem: docker logs \$(docker ps -aqf name=qdrant)"
  exit 1
}

api_running() { [ -f "$API_PID" ] && kill -0 "$(cat "$API_PID")" 2>/dev/null; }
api_healthy() { curl -s --max-time 2 -o /dev/null "${API_URL}/health"; }

cmd_up() {
  activate_env
  if [ -n "$(qdrant_url)" ]; then cmd_qdrant_up; fi
  bash scripts/ollama_local.sh start
  if api_running; then
    echo "API da chay (pid $(cat "$API_PID"), ${API_URL})."
    return 0
  fi
  if api_healthy; then
    echo "LOI: cong ${API_PORT} dang co chuong trinh khac (co the la 'make run' o terminal khac)."
    exit 1
  fi
  mkdir -p "$RUN_DIR"
  echo "Khoi dong API: ${API_URL} (log: $API_LOG)"
  nohup python -m uvicorn assistant.main:app --host 127.0.0.1 --port "$API_PORT" \
    >"$API_LOG" 2>&1 &
  echo $! >"$API_PID"
  for _ in $(seq 1 30); do
    if api_healthy; then
      echo "API san sang. Kiem tra LLM: $(curl -s --max-time 5 "${API_URL}/health/llm" | head -c 200)"
      return 0
    fi
    if ! api_running; then
      echo "LOI: API thoat som. 20 dong log cuoi:"
      tail -20 "$API_LOG"
      rm -f "$API_PID"
      exit 1
    fi
    sleep 1
  done
  echo "LOI: het 30 giay API van chua san sang. Xem: $API_LOG"
  exit 1
}

cmd_stop_api() {
  if api_running; then
    pid="$(cat "$API_PID")"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -9 "$pid" 2>/dev/null || true
    echo "Da tat API (pid ${pid})."
  fi
  rm -f "$API_PID"
}

cmd_chat() {
  cmd_up
  echo
  exec python scripts/chat_cli.py --api "$API_URL"
}

cmd_web() {
  cmd_up
  echo
  echo "Giao dien web: ${API_URL}"
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$API_URL" >/dev/null 2>&1 || true
  fi
}

cmd_status() {
  if api_running; then
    echo "API: DANG CHAY (pid $(cat "$API_PID"), ${API_URL})"
  else
    echo "API: KHONG CHAY"
  fi
  bash scripts/ollama_local.sh status
  echo "--- cot PROCESSOR o tren phai la '100% GPU'; neu thay CPU/GPU la model khong vua VRAM"
  nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader 2>/dev/null || true
}

case "${1:-}" in
  up)      cmd_up ;;
  web)     cmd_web ;;
  chat)    cmd_chat ;;
  restart) cmd_stop_api; cmd_up ;;
  status)  cmd_status ;;
  logs)    tail -n 100 -f "$API_LOG" ;;
  down)    cmd_stop_api; bash scripts/ollama_local.sh stop
           if [ -n "$(qdrant_url)" ]; then compose --profile qdrant stop qdrant || true; fi ;;
  qdrant-up) cmd_qdrant_up ;;
  qdrant-down) compose --profile qdrant stop qdrant ;;
  *) echo "Dung: bash scripts/dev.sh {web|chat|up|restart|status|logs|down|qdrant-up}"; exit 1 ;;
esac
