"""给 AstrBot 4.29.1 的 WakingCheckStage 补一个空消息保护。

原版这里直接取 messages[0]，QQ 的空事件（post_type 不是 message 的系统消息）
会触发 IndexError。只在原条件前加一个 `messages and`，其他代码一律不动，
这样不会覆盖别的插件已经改过的跟随输入逻辑。

用法：
    python patches/fix_waking_stage.py --root D:\\QQBot\\AstrBot
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"D:\QQBot\AstrBot")
    args = parser.parse_args()

    path = Path(args.root) / "astrbot" / "core" / "pipeline" / "waking_check" / "stage.py"
    if not path.exists():
        print(f"找不到 {path}")
        return 1

    text = path.read_text(encoding="utf-8")
    if re.search(r"(?m)^[ \t]*messages[ \t]*\r?\n[ \t]*and not event\.is_private_chat\(\)", text):
        print("空消息保护已经在了")
        return 0

    pattern = re.compile(
        r"(?m)^(?P<indent>[ \t]*)not event\.is_private_chat\(\)\r?\n"
        r"(?P<indent2>[ \t]*)and isinstance\(messages\[0\], At\)"
    )

    def repl(match: re.Match[str]) -> str:
        indent = match.group("indent")
        indent2 = match.group("indent2")
        return (
            f"{indent}messages\n"
            f"{indent}and not event.is_private_chat()\n"
            f"{indent2}and isinstance(messages[0], At)"
        )

    updated, count = pattern.subn(repl, text, count=1)
    if count != 1:
        print("没找到原条件，可能版本不对；没有改动。")
        return 1

    backup = path.with_suffix(".py.bak")
    if not backup.exists():
        backup.write_text(text, encoding="utf-8")
    path.write_text(updated, encoding="utf-8")
    print(f"已补空消息保护：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
