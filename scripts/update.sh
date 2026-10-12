#!/usr/bin/env bash
# Áp bản cập nhật code: zip chỉ chứa các file thay đổi (giữ đường dẫn trong project).
#
#   make update                      lấy RAG_LLM_*.zip mới nhất trong ~/Downloads
#   make update ZIP=đường/dẫn.zip    chỉ định file zip
#   make rollback                    khôi phục các file trước lần cập nhật gần nhất
#
# Các bước: sao lưu file cũ sắp bị ghi đè -> giải nén đè -> chạy test -> khởi động lại API
# -> chuyển zip đã áp sang ~/Downloads/RAG_LLM_applied/ để lần sau không áp lại.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
BACKUP_ROOT="$ROOT/.run/backup"
ENV_NAME="${RAG_ENV:-rag_llm}"

activate_env() {
  if [ "${CONDA_DEFAULT_ENV:-}" = "$ENV_NAME" ]; then return 0; fi
  set +u
  # shellcheck disable=SC1091
  source "$(conda info --base)/etc/profile.d/conda.sh"
  conda activate "$ENV_NAME"
  set -u
}

cmd_rollback() {
  last="$(ls -1d "$BACKUP_ROOT"/*/ 2>/dev/null | sort | tail -1 || true)"
  [ -n "$last" ] || { echo "Khong co ban sao luu nao."; exit 1; }
  echo "Khoi phuc tu: $last"
  (cd "$last" && find . -type f ! -name .new_files -print | while read -r f; do
    mkdir -p "$ROOT/$(dirname "$f")"; cp "$f" "$ROOT/$f"; echo "  khoi phuc ${f#./}"; done)
  if [ -f "$last/.new_files" ]; then  # file mới do bản cập nhật thêm vào -> xoá
    while read -r f; do [ -n "$f" ] && rm -fv "$ROOT/$f" | sed 's/^/  xoa /'; done <"$last/.new_files"
  fi
  rm -rf "$last"
  bash scripts/dev.sh restart || true
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

  ts="$(date +%Y%m%d-%H%M%S)"
  backup="$BACKUP_ROOT/$ts"
  mkdir -p "$backup"
  : >"$backup/.new_files"
  for f in "${FILES[@]}"; do
    if [ -f "$ROOT/$f" ]; then
      mkdir -p "$backup/$(dirname "$f")"
      cp "$ROOT/$f" "$backup/$f"
      echo "  sua  $f"
    else
      echo "$f" >>"$backup/.new_files"
      echo "  moi  $f"
    fi
  done
  unzip -oq "$ZIP" -d "$ROOT"
  chmod +x scripts/*.sh 2>/dev/null || true

  mkdir -p "$HOME/Downloads/RAG_LLM_applied"
  mv "$ZIP" "$HOME/Downloads/RAG_LLM_applied/" 2>/dev/null || true
  echo "Sao luu file cu: $backup   (hoan tac: make rollback)"

  activate_env
  if [[ " ${FILES[*]} " == *" pyproject.toml "* ]]; then  # bản cập nhật đổi danh sách thư viện
    echo; echo "Ban cap nhat co thu vien moi, dang cai..."
    python -m pip install -q -e ".[dev]"
  fi
  echo; echo "Chay test..."
  if ! python -m pytest -q; then
    echo; echo "CANH BAO: test loi. Gui log tren cho Claude, hoac 'make rollback' de quay lai."
    exit 1
  fi
  echo
  bash scripts/dev.sh restart
  echo; echo "Xong. F5 lai trang web."
}

case "${1:-update}" in
  update)   shift || true; cmd_update "${1:-}" ;;
  rollback) cmd_rollback ;;
  *) echo "Dung: bash scripts/update.sh [update [file.zip]|rollback]"; exit 1 ;;
esac
