"""下载本机语音识别模型（faster-whisper small）。

用法：
    D:\\QQBot\\AstrBot\\.venv\\Scripts\\python.exe D:\\QQBot\\scripts\\fetch_asr_model.py

默认下到 models\\asr\\faster-whisper-small，约 460MB；已有 model.bin 就跳过。
国内下载慢会自动走 hf-mirror，可用 --endpoint 换源。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("QQBOT_ROOT", r"D:\QQBot"))
DEFAULT_DIR = ROOT / "models" / "asr" / "faster-whisper-small"
DEFAULT_REPO = "Systran/faster-whisper-small"
DEFAULT_ENDPOINT = "https://hf-mirror.com"


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 faster-whisper 语音识别模型")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="HuggingFace 模型名")
    parser.add_argument("--dir", default=str(DEFAULT_DIR), help="保存目录")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="下载源")
    args = parser.parse_args()

    target = Path(args.dir)
    if (target / "model.bin").exists():
        print(f"模型已存在：{target}")
        return 0

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("缺少 huggingface_hub，先在 AstrBot 的 python 里装 faster-whisper：")
        print(r"  D:\QQBot\AstrBot\.venv\Scripts\python.exe -m pip install faster-whisper")
        return 1

    target.mkdir(parents=True, exist_ok=True)
    print(f"下载 {args.repo} -> {target}")
    snapshot_download(args.repo, local_dir=str(target), endpoint=args.endpoint or None)
    if not (target / "model.bin").exists():
        print("下完了但没找到 model.bin，检查下载源或磁盘空间。")
        return 1
    print(f"完成：{target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
