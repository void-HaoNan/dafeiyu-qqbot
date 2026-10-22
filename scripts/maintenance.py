"""一键开关维护模式。

用法：
    python scripts/maintenance.py on
    python scripts/maintenance.py off
    python scripts/maintenance.py status
    python scripts/maintenance.py message "本鱼在升级，晚点再来。"

改的是 data/dafeiyu/config.json，v2 引擎每条消息都会重读，不用重启。
"""

import json
import os
import pathlib
import sys

ROOT = pathlib.Path(os.environ.get("QQBOT_ROOT", r"D:\QQBot"))
CONF = ROOT / "data" / "dafeiyu" / "config.json"

DEFAULT_MESSAGE = "唔，本鱼正在升级，暂时没办法好好聊天，等我弄好了再叫你。"


def load() -> dict:
    try:
        data = json.loads(CONF.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("modules", {})
    data["modules"].setdefault("maintenance", {})
    section = data["modules"]["maintenance"]
    section.setdefault("enabled", False)
    section.setdefault("message", DEFAULT_MESSAGE)
    section.setdefault("cooldown_seconds", 120)
    section.setdefault("admin_bypass", True)
    return data


def save(data: dict) -> None:
    CONF.parent.mkdir(parents=True, exist_ok=True)
    CONF.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    action = argv[0]
    data = load()
    section = data["modules"]["maintenance"]

    if action in ("on", "off"):
        section["enabled"] = action == "on"
        save(data)
        print(f"维护模式：{'开' if section['enabled'] else '关'}")
        print(f"配置：{CONF}")
    elif action == "status":
        print(f"维护模式：{'开' if section['enabled'] else '关'}")
        print(f"提示语：{section['message']}")
        print(f"冷却：{section['cooldown_seconds']} 秒")
        print(f"管理员放行：{section['admin_bypass']}")
    elif action == "message" and len(argv) > 1:
        section["message"] = " ".join(argv[1:])
        save(data)
        print(f"提示语已改成：{section['message']}")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
