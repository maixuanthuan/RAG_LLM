"""Client chat trên terminal: hỏi đáp có nguồn, upload tài liệu, ghim tài liệu để hỏi sâu.

Lệnh:
  /upload <file|thư mục> [nhóm]   nạp tài liệu qua API (nhóm mặc định: uploads)
  /docs                           liệt kê tài liệu (đánh số để dùng với /focus)
  /focus <số|id> [...]            chỉ hỏi về các tài liệu này (đưa toàn văn cho AI nếu đủ ngắn)
  /all                            bỏ ghim, hỏi trên toàn bộ kho
  /plain                          bật/tắt chat thường (không dùng tài liệu)
  /delete <số|id>                 xoá tài liệu khỏi kho
  /reset                          xoá lịch sử hội thoại
  /exit                           thoát
"""

import argparse
import json
from pathlib import Path

import httpx

from assistant.ingestion.parsers import SUPPORTED_SUFFIXES


class Session:
    def __init__(self, http: httpx.Client, api: str) -> None:
        self.http = http
        self.api = api
        self.history: list[dict] = []
        self.docs: list[dict] = []  # kết quả /docs gần nhất, để chọn theo số
        self.focus: list[dict] = []
        self.use_documents = True

    # ---- tài liệu ----
    def upload(self, args: list[str]) -> None:
        if not args:
            print("Dùng: /upload <file|thư mục> [nhóm]")
            return
        path, group = Path(args[0]).expanduser(), (args[1] if len(args) > 1 else "uploads")
        if path.is_dir():
            files = sorted(
                p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
            )
        elif path.is_file():
            files = [path]
        else:
            print(f"Không thấy: {path}")
            return
        for f in files:
            print(f"  nạp {f.name} ...", end=" ", flush=True)
            with f.open("rb") as fh:
                r = self.http.post(
                    f"{self.api}/api/documents", files={"file": (f.name, fh)}, data={"group": group}
                )
            if r.status_code != 200:
                print(f"LỖI {r.status_code}: {r.json().get('detail')}")
                continue
            d = r.json()
            print(f"{d['status']} ({d['chunks']} chunk)")
            for w in d["warnings"]:
                print(f"    cảnh báo: {w}")

    def list_docs(self) -> None:
        self.docs = self.http.get(f"{self.api}/api/documents").json()
        if not self.docs:
            print("(kho trống, dùng /upload để nạp)")
        for i, d in enumerate(self.docs, start=1):
            mark = "*" if any(f["id"] == d["id"] for f in self.focus) else " "
            print(f" {mark}{i:>3}. [{d['group']}] {d['filename']}  ({d['chunks']} chunk)")

    def _pick(self, keys: list[str]) -> list[dict]:
        if not self.docs:
            self.docs = self.http.get(f"{self.api}/api/documents").json()
        picked = []
        for k in keys:
            if k.isdigit() and 1 <= int(k) <= len(self.docs):
                picked.append(self.docs[int(k) - 1])
            else:
                match = [d for d in self.docs if d["id"] == k or d["filename"] == k]
                if match:
                    picked.append(match[0])
                else:
                    print(f"Không có tài liệu '{k}' (xem /docs)")
        return picked

    def set_focus(self, keys: list[str]) -> None:
        self.focus = self._pick(keys)
        if self.focus:
            print("Đang ghim: " + ", ".join(d["filename"] for d in self.focus))

    def delete(self, keys: list[str]) -> None:
        for d in self._pick(keys):
            r = self.http.delete(f"{self.api}/api/documents/{d['id']}")
            print(f"  xoá {d['filename']}: {'ok' if r.status_code == 200 else r.text}")
            self.focus = [f for f in self.focus if f["id"] != d["id"]]
        self.docs = []

    # ---- hỏi đáp ----
    def ask(self, q: str) -> None:
        self.history.append({"role": "user", "content": q})
        body = {"messages": self.history, "use_documents": self.use_documents}
        if self.focus:
            body["source_ids"] = [d["id"] for d in self.focus]
        answer, sources, notes, stats = "", [], [], {}
        print("AI: ", end="", flush=True)
        with self.http.stream("POST", f"{self.api}/api/chat", json=body) as r:
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                if ev["type"] == "delta":
                    answer += ev["text"]
                    print(ev["text"], end="", flush=True)
                elif ev["type"] == "sources":
                    sources = ev["sources"]
                elif ev["type"] == "warning":
                    notes.append(ev["message"])
                elif ev["type"] == "error":
                    print(f"\n[LỖI] {ev['message']}")
                elif ev["type"] == "done":
                    stats = ev["stats"]
        print()
        if sources:
            print("Nguồn:")
            for s in sources:
                where = " | ".join(
                    x
                    for x in (
                        f"mục: {s['section']}" if s["section"] and not s["full"] else "",
                        f"trang {s['page']}" if s["page"] and not s["full"] else "",
                        "toàn văn" if s["full"] else "",
                    )
                    if x
                )
                print(f"  [{s['n']}] {s['document']}" + (f"  ({where})" if where else ""))
        for n in notes:
            print(f"[!] {n}")
        print(f"[{stats}]\n")
        if answer:
            self.history.append({"role": "assistant", "content": answer})
        else:
            self.history.pop()

    def prompt(self) -> str:
        if not self.use_documents:
            return "Bạn (chat thường): "
        if self.focus:
            return f"Bạn ({len(self.focus)} tài liệu ghim): "
        return "Bạn: "


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8080")
    args = parser.parse_args()

    print(f"Chat với {args.api}")
    print(__doc__.split("Lệnh:", 1)[1])
    with httpx.Client(timeout=None) as http:
        s = Session(http, args.api)
        while True:
            try:
                q = input(s.prompt()).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q:
                continue
            cmd, *rest = q.split()
            if cmd == "/exit":
                break
            elif cmd == "/reset":
                s.history.clear()
                print("(đã xoá lịch sử)\n")
            elif cmd == "/upload":
                s.upload(rest)
            elif cmd == "/docs":
                s.list_docs()
            elif cmd == "/focus":
                s.set_focus(rest)
            elif cmd == "/all":
                s.focus = []
                print("(hỏi trên toàn bộ kho)")
            elif cmd == "/plain":
                s.use_documents = not s.use_documents
                print("(chat thường)" if not s.use_documents else "(dùng tài liệu)")
            elif cmd == "/delete":
                s.delete(rest)
            elif cmd.startswith("/"):
                print("Lệnh không hợp lệ.")
            else:
                try:
                    s.ask(q)
                except httpx.HTTPError as e:
                    s.history.pop()
                    print(f"\n[LỖI kết nối] {e}\n")


if __name__ == "__main__":
    main()
