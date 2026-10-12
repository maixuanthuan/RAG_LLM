#!/usr/bin/env bash
# Áp bản cập nhật code: zip chỉ chứa các file thay đổi (giữ đường dẫn trong project).
#
#   make update                      lấy RAG_LLM_*.zip mới nhất trong ~/Downloads
#   make update ZIP=đường/dẫn.zip    chỉ định file zip
#
# Các bước: giải nén đè -> xoá zip -> chạy test -> khởi động lại API.
# Không tự sao lưu: project đã có git. Quay lại bản trước: git restore .  (file mới: git clean -n)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
ENV_NAME="${RAG_ENV:-rag_llm}"

activate_env() {
  if [ "${CONDA_DEFAULT_ENV:-}" = "$ENV_NAME" ]; then return 0; fi
  set +u
  # shellcheck disable=SC1091
  source "$(conda info --base)/etc/profile.d/conda.sh"
  conda activate "$ENV_NAME"
  set -u
}

cmd_update() {
  ZIP="${1:-}"
  if [ -z "$ZIP" ]; then
    ZIP="$(ls -t "$HOME"/Downloads/RAG_LLM_*.zip 2>/dev/null | head -1 || true)"
  fi
  [ -n "$ZIP" ] && [ -f "$ZIP" ] || { echo "LOI: khong thay file RAG_LLM_*.zip trong ~/Downloads"; exit 1; }

  mapfile -t FILES < <(unzip -Z1 "$ZIP" | grep -v '/$')
  for f in "${FILES[@]}"; do  # chặn đường dẫn bất thường
    case "$f" in /*|*..*) echo "LOI: duong dan khong hop le trong zip: $f"; exit 1 ;; esac
  done
  echo "Ap dung $(basename "$ZIP") (${#FILES[@]} file):"

  for f in "${FILES[@]}"; do
    if [ -f "$ROOT/$f" ]; then echo "  sua  $f"; else echo "  moi  $f"; fi
  done
  unzip -oq "$ZIP" -d "$ROOT"
  chmod +x scripts/*.sh 2>/dev/null || true
  rm -f "$ZIP"  # đã áp xong, xoá để lần sau không áp lại

  activate_env
  if [[ " ${FILES[*]} " == *" pyproject.toml "* ]]; then  # bản cập nhật đổi danh sách thư viện
    echo; echo "Ban cap nhat co thu vien moi, dang cai..."
    python -m pip install -q -e ".[dev]"
  fi
  echo; echo "Chay test..."
  if ! python -m pytest -q; then
    echo; echo "CANH BAO: test loi. Gui log tren cho Claude, hoac 'git restore .' de quay lai."
    exit 1
  fi
  echo
  bash scripts/dev.sh restart
  echo; echo "Xong. F5 lai trang web."
}

case "${1:-update}" in
  update) shift || true; cmd_update "${1:-}" ;;
  *) echo "Dung: bash scripts/update.sh [update [file.zip]]"; exit 1 ;;
esac
