#!/usr/bin/env bash
# Chạy một Ollama RIÊNG của project (cổng 11435), ép dùng GPU NVIDIA qua Vulkan.
# Không sửa service Ollama của hệ thống, không cần sudo. Tắt bằng: stop.
#
# Dùng:  bash scripts/ollama_local.sh {start|stop|status|pull|check|restart}
#
# Biến có thể đổi (đặt trước lệnh):
#   OLLAMA_LOCAL_PORT       cổng Ollama riêng            (mặc định 11435)
#   GGML_VK_VISIBLE_DEVICES chỉ số GPU Vulkan            (mặc định 0, vì chỉ còn driver NVIDIA)
#   VK_ICD_FILENAMES        file driver Vulkan NVIDIA    (mặc định tự tìm nvidia_icd.json)
#   LLM_MODEL               model cần kéo/thử            (mặc định lấy từ .env, rồi qwen2.5:3b)
#   OLLAMA_CONTEXT_LENGTH   độ dài context (token)       (mặc định 8192; Ollama mặc định 4096,
#                           không đủ cho RAG: nguồn + lịch sử + câu trả lời bị cắt im lặng)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.run"
PID_FILE="$RUN_DIR/ollama.pid"
LOG_FILE="$RUN_DIR/ollama.log"

PORT="${OLLAMA_LOCAL_PORT:-11435}"
VK_DEVICE="${GGML_VK_VISIBLE_DEVICES:-0}"
HOST="127.0.0.1:${PORT}"
CTX="${OLLAMA_CONTEXT_LENGTH:-8192}"

# Chỉ cho Vulkan thấy driver NVIDIA, để llvmpipe (giả lập CPU) và Intel không bị chọn nhầm
ICD_FILE="${VK_ICD_FILENAMES:-}"
if [ -z "$ICD_FILE" ]; then
  for f in /usr/share/vulkan/icd.d/nvidia_icd.json /etc/vulkan/icd.d/nvidia_icd.json; do
    if [ -f "$f" ]; then ICD_FILE="$f"; break; fi
  done
fi

MODEL="${LLM_MODEL:-}"
if [ -z "$MODEL" ] && [ -f "$ROOT/.env" ]; then
  MODEL="$(grep -E '^LLM_MODEL=' "$ROOT/.env" | tail -1 | cut -d= -f2- || true)"
fi
MODEL="${MODEL:-qwen2.5:3b}"

is_running() {
  [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

api_up() {
  curl -s --max-time 2 -o /dev/null "http://${HOST}/api/version"
}

need_ollama() {
  command -v ollama >/dev/null 2>&1 || { echo "LOI: chua co lenh 'ollama' trong PATH."; exit 1; }
}

cmd_start() {
  need_ollama
  if is_running; then
    echo "Ollama rieng da chay (pid $(cat "$PID_FILE"), cong ${PORT})."
    return 0
  fi
  if api_up; then
    echo "LOI: cong ${PORT} dang bi chuong trinh khac dung. Doi OLLAMA_LOCAL_PORT."
    exit 1
  fi
  mkdir -p "$RUN_DIR"
  if [ -z "$ICD_FILE" ]; then
    echo "CANH BAO: khong thay nvidia_icd.json, Ollama co the chon nham llvmpipe (CPU)."
  fi
  echo "Khoi dong Ollama rieng: cong ${PORT}, GPU=${VK_DEVICE}, ctx=${CTX}, ICD=${ICD_FILE:-khong}"
  VK_ICD_FILENAMES="$ICD_FILE" GGML_VK_VISIBLE_DEVICES="$VK_DEVICE" OLLAMA_HOST="$HOST" \
    OLLAMA_CONTEXT_LENGTH="$CTX" nohup ollama serve >"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  for _ in $(seq 1 30); do
    if api_up; then
      echo "San sang. Log: $LOG_FILE"
      echo "Nho dat trong .env:  LLM_BASE_URL=http://localhost:${PORT}/v1"
      return 0
    fi
    if ! is_running; then
      echo "LOI: Ollama thoat som. 20 dong log cuoi:"
      tail -20 "$LOG_FILE"
      rm -f "$PID_FILE"
      exit 1
    fi
    sleep 1
  done
  echo "LOI: het 30 giay van chua san sang. Xem log: $LOG_FILE"
  exit 1
}

cmd_stop() {
  if is_running; then
    pid="$(cat "$PID_FILE")"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    kill -9 "$pid" 2>/dev/null || true
    echo "Da tat Ollama rieng (pid ${pid})."
  else
    echo "Ollama rieng khong chay."
  fi
  rm -f "$PID_FILE"
}

cmd_status() {
  if is_running; then
    echo "Ollama rieng: DANG CHAY (pid $(cat "$PID_FILE"), cong ${PORT})"
  else
    echo "Ollama rieng: KHONG CHAY"
    return 0
  fi
  echo "--- model dang nap:"
  OLLAMA_HOST="$HOST" ollama ps || true
  echo "--- tien trinh llama-server tren GPU (neu dung A3000 se thay VRAM ~ 2 GB):"
  nvidia-smi 2>/dev/null | grep -i llama-server || echo "(khong thay llama-server trong nvidia-smi)"
}

cmd_pull() {
  need_ollama
  is_running || cmd_start
  echo "Keo model: ${MODEL}"
  OLLAMA_HOST="$HOST" ollama pull "$MODEL"
}

cmd_check() {
  need_ollama
  is_running || cmd_start
  echo "Sinh thu toi da 200 token voi ${MODEL}..."
  curl -s "http://${HOST}/api/generate" -d "{
    \"model\": \"${MODEL}\", \"stream\": false, \"keep_alive\": \"2m\",
    \"prompt\": \"Viet mot doan van ngan ve loi ich cua viec doc sach.\",
    \"options\": {\"num_predict\": 200}
  }" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print(d.get("response", d))
n, t = d.get("eval_count"), d.get("eval_duration")
if n and t:
    print(f"\n[toc do sinh: {n / (t / 1e9):.1f} token/s, {n} token]")
'
  echo
  cmd_status
}

case "${1:-}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status)  cmd_status ;;
  pull)    cmd_pull ;;
  check)   cmd_check ;;
  *) echo "Dung: bash scripts/ollama_local.sh {start|stop|restart|status|pull|check}"; exit 1 ;;
esac
