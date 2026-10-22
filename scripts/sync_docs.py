# -*- coding: utf-8 -*-
"""把仓库文档同步到指定目录，方便放到自己的笔记/工作区里看。

用法：
    python scripts/sync_docs.py <目标目录>
    set QQBOT_DOCS_DIR=<目标目录> && python scripts/sync_docs.py
"""

import os
import shutil
import sys
from pathlib import Path

SRC = Path(os.environ.get("QQBOT_REPO") or Path(__file__).resolve().parents[1])
if not (SRC / "docs" / "CONFIG.md").exists():
    fallback = Path(r"D:\dafeiyu-qqbot")
    if fallback.exists():
        SRC = fallback

TOP = ["README.md", "ARCHITECTURE.md", "INSTALL.md", "THIRD_PARTY.md"]
SUB = [("docs/CONFIG.md", "CONFIG.md"), ("docs/MODULES.md", "MODULES.md"), ("docs/DIRECTORY.md", "DIRECTORY.md"), ("docs/V2_TEST.md", "V2_TEST.md")]


def main() -> int:
    dst_arg = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("QQBOT_DOCS_DIR", "")
    if not dst_arg:
        print("用法：python scripts/sync_docs.py <目标目录>，或先设 QQBOT_DOCS_DIR")
        return 2
    dst = Path(dst_arg).expanduser()
    dst.mkdir(parents=True, exist_ok=True)
    copied = 0
    for name in TOP:
        src = SRC / name
        if src.exists():
            shutil.copy2(src, dst / name)
            copied += 1
    for src_rel, dst_name in SUB:
        src = SRC / src_rel
        if src.exists():
            shutil.copy2(src, dst / dst_name)
            copied += 1
    print(f"同步完成：{copied} 个文件 -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
