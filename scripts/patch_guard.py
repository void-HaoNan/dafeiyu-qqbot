# -*- coding: utf-8 -*-
"""检查本地补丁还在不在：AstrBot 核心两个文件 + 点歌插件两处行为。

用法：
    python scripts/patch_guard.py           # 只检查，退出码 1 表示有缺失
    python scripts/patch_guard.py --fix     # 能自动补的核心补丁顺手补上
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(os.environ.get("QQBOT_ROOT") or pathlib.Path(__file__).resolve().parents[1])
ASTRBOT = ROOT / "AstrBot"
REPO_PATCHES = ROOT / "patches" / "astrbot-4.29.1"
MUSIC = ASTRBOT / "data" / "plugins" / "astrbot_plugin_music" / "main.py"

PAIRS = [
    ("astrbot/core/pipeline/whitelist_check/stage.py", "白名单热加载"),
    ("astrbot/builtin_stars/astrbot/group_chat_context.py", "群图描述"),
]


def core_missing() -> list[str]:
    bad = []
    for rel, name in PAIRS:
        src = REPO_PATCHES / rel
        dst = ASTRBOT / rel
        if not src.exists():
            continue
        if not dst.exists() or src.read_bytes() != dst.read_bytes():
            bad.append(name)
    return bad


def music_missing() -> bool:
    try:
        text = MUSIC.read_text(encoding="utf-8")
    except OSError:
        return False
    return "_dafeiyu_collected_song" not in text


def fix_core() -> None:
    script = ROOT / "patches" / "apply_core_patches.ps1"
    if script.exists():
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(script), "-AstrBotRoot", str(ASTRBOT)],
            capture_output=True, timeout=60,
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true")
    args = ap.parse_args()

    bad = core_missing()
    if bad and args.fix:
        fix_core()
        bad = core_missing()
    if bad:
        print("核心补丁缺失：" + "、".join(bad) + "（重打后重启 AstrBot）")
    else:
        print("核心补丁：正常")

    if music_missing():
        print("点歌插件补丁缺失：播放会重新自动收藏、收藏不会限一首，重装插件后要重打")
    else:
        print("点歌插件补丁：正常")

    return 1 if (bad or music_missing()) else 0


if __name__ == "__main__":
    sys.exit(main())
