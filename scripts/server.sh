#!/usr/bin/env bash
# Chạy hệ thống trên server GPU bằng Docker (docker-compose.server.yml). Không cần conda.
#
#   bash scripts/server.sh check    kiểm tra Docker, Compose, GPU trong Docker   (make server-check)
#   bash scripts/server.sh up       build + bật / cập nhật sau khi đổi code      (make server-up)
#   bash scripts/server.sh down     tắt, dữ liệu giữ nguyên                     (make server-down)
#   bash scripts/server.sh status   container, model trên GPU, VRAM              (make server-status)
#   bash scripts/server.sh logs     xem log API (Ctrl+C để thoát)                (make server-logs)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
FILE="docker-compose.server.yml"
PROJECT="rag_llm"

env_get() {  # đọc biến trong .env (cho cổng web)
  grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//' | tr -d '"' || true
}
PORT="$(env_get API_PORT)"; PORT="${PORT:-8080}"

compose() {  # docker compose (plugin v2) hoặc docker-compose (bản rời)
  if docker compose version >/dev/null 2>&1; then
    docker compose -p "$PROJECT" -f "$FILE" "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose -p "$PROJECT" -f "$FILE" "$@"
  else
    echo "LOI: chua co Docker Compose. Cai bang:  sudo apt install docker-compose-v2"
    return 1
  fi
}

cmd_check() {
  local ok=1
  command -v docker >/dev/null 2>&1 && echo "✓ Docker: $(docker --version)" \
    || { echo "✗ Chua cai Docker"; ok=0; }
  compose version >/dev/null 2>&1 && echo "✓ Docker Compose" || ok=0
  if command -v nvidia-smi >/dev/null 2>&1; then
    echo "✓ NVIDIA driver:"
    nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader | sed 's/^/    /'
  else
    echo "✗ Khong thay nvidia-smi (chua cai driver NVIDIA)"; ok=0
  fi
  if docker run --rm --gpus all ubuntu:22.04 true >/dev/null 2>&1; then
    echo "✓ Docker dung duoc GPU (nvidia-container-toolkit)"
  else
    echo "✗ Docker chua dung duoc GPU: cai nvidia-container-toolkit roi 'sudo systemctl restart docker'"
    ok=0
  fi
  [ "$ok" = 1 ] && echo "=> San sang: make server-up" || { echo "=> Chua san sang"; return 1; }
}

cmd_up() {
  [ -f .env ] || { cp .env.example .env; echo "Tao .env tu .env.example"; }
  mkdir -p data
  echo "Build + bat he thong (lan dau tai model co the mat vai phut)..."
  compose up -d --build --remove-orphans
  echo "Cho API san sang (lan dau co the dung lai kho vector)..."
  for _ in $(seq 1 300); do
    if curl -s --max-time 2 -o /dev/null "http://localhost:${PORT}/health"; then
      echo "San sang: http://localhost:${PORT}"
      echo "Kiem tra LLM: $(curl -s --max-time 10 "http://localhost:${PORT}/health/llm" | head -c 200)"
      return 0
    fi
    sleep 2
  done
  echo "LOI: API chua san sang sau 10 phut. Xem: make server-logs"
  exit 1
}

cmd_status() {
  compose ps
  echo "--- model dang nam tren GPU:"
  compose exec -T ollama ollama ps || true
  if command -v nvidia-smi >/dev/null 2>&1; then
    echo "--- VRAM:"
    nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader
  fi
}

case "${1:-}" in
  check)  cmd_check ;;
  up)     cmd_up ;;
  down)   compose down ;;
  status) cmd_status ;;
  logs)   compose logs -f --tail 200 api ;;
  *) echo "Dung: bash scripts/server.sh {check|up|down|status|logs}"; exit 1 ;;
esac
