"""Tạo bộ tài liệu mẫu (hư cấu) để thử pipeline: python scripts/make_sample_docs.py"""

import argparse
from pathlib import Path

from assistant.devtools.sample_docs import make_sample_docs


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="data/sample_docs")
    args = p.parse_args()
    files = make_sample_docs(Path(args.out))
    print(f"Đã tạo {len(files)} file trong {args.out}:")
    for f in files:
        print(" ", f)


if __name__ == "__main__":
    main()
