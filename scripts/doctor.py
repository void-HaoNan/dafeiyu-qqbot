# -*- coding: utf-8 -*-
"""环境体检：装完之后跑一遍，缺什么按提示补。

用法（用 AstrBot 的 venv 跑最省事）：
    <项目根>\\AstrBot\\.venv\\Scripts\\python.exe <项目根>\\scripts\\doctor.py

只读检查，不改任何文件。退出码：有 FAIL 返回 1，否则 0。
"""

from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("QQBOT_ROOT") or Path(__file__).resolve().parents[1]).resolve()
ASTRBOT = ROOT / "AstrBot"
PLUGIN = ASTRBOT / "data" / "plugins" / "astrbot_plugin_dafeiyu"
CONFIG = ROOT / "data" / "dafeiyu" / "config.json"
ACCESS = ROOT / "data" / "access_lists.json"
DB = ASTRBOT / "data" / "data_v4.db"
PANEL = ROOT / "panel" / "app.py"
CMD_CFG = ASTRBOT / "data" / "cmd_config.json"

results: list[tuple[str, str, str]] = []


def add(level: str, title: str, detail: str = "") -> None:
    results.append((level, title, detail))


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def check_astrbot() -> None:
    if not ASTRBOT.is_dir():
        hint = "；当前像是仓库目录，设 QQBOT_ROOT 指向安装根再跑" if (ROOT / "engines").is_dir() else ""
        add("FAIL", "AstrBot 目录", f"找不到 {ASTRBOT}，先按 AstrBot 官方文档装好{hint}")
        return
    pyproject = ASTRBOT / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
        ver = m.group(1) if m else ""
        if ver.startswith("4.29."):
            add("OK", "AstrBot 版本", ver)
        else:
            add("WARN", "AstrBot 版本", f"{ver or '未知'}，补丁按 4.29.1 做的")
    else:
        add("WARN", "AstrBot 版本", "没有 pyproject.toml，跳过版本检查")
    if not PLUGIN.is_dir():
        add("FAIL", "插件", f"没装到 {PLUGIN}，先跑 install.ps1")
    elif not (PLUGIN / "main.py").exists():
        add("FAIL", "插件", "插件目录在，但缺少 main.py，重新拷一次")
    else:
        add("OK", "插件", str(PLUGIN))


def check_patches() -> None:
    if not (ROOT / "patches").is_dir():
        add("WARN", "核心补丁", "项目里没有 patches 目录，跳过检查（重新跑 install.ps1 会带上）")
        return
    pairs = [
        ("astrbot/core/pipeline/whitelist_check/stage.py", "白名单热加载补丁"),
        ("astrbot/builtin_stars/astrbot/group_chat_context.py", "群图描述补丁"),
    ]
    for rel, name in pairs:
        src = ROOT / "patches" / "astrbot-4.29.1" / rel
        dst = ASTRBOT / rel
        if not src.exists():
            continue
        if not dst.exists():
            add("FAIL", name, f"目标文件不存在：{dst}")
        elif src.read_bytes() == dst.read_bytes():
            add("OK", name, "已打")
        else:
            add("WARN", name, "没打或版本不同，跑 install.ps1 -ApplyPatches")

    ctx = ASTRBOT / "astrbot" / "builtin_stars" / "astrbot" / "group_chat_context.py"
    try:
        text = ctx.read_text(encoding="utf-8", errors="replace")
        if "_no_save" in text:
            add("OK", "群上下文不写历史", "已标 _no_save")
        else:
            add("WARN", "群上下文不写历史", "没标 _no_save，群上下文会一直进历史")
    except OSError:
        add("WARN", "群上下文不写历史", f"读不到 {ctx}")

    stage = ASTRBOT / "astrbot" / "core" / "pipeline" / "waking_check" / "stage.py"
    try:
        text = stage.read_text(encoding="utf-8", errors="replace")
        if re.search(r"(?m)^[ \t]*messages[ \t]*\r?\n[ \t]*and not event\.is_private_chat\(\)", text):
            add("OK", "空消息保护", "已加 messages and")
        else:
            add("WARN", "空消息保护", "没加，QQ 空事件会 IndexError；跑 apply_core_patches.ps1")
    except OSError:
        add("WARN", "空消息保护", f"读不到 {stage}")


def check_config() -> None:
    cfg = read_json(CONFIG)
    if not cfg:
        add("FAIL", "统一配置", f"读不到 {CONFIG}，先跑 install.ps1")
    else:
        for key, label in (("admin_qq", "管理员 QQ"), ("bot_qq", "机器人 QQ"), ("group", "主群号")):
            val = str(cfg.get(key) or "").strip()
            if not val:
                add("FAIL", label, f"{CONFIG.name} 里没填 {key}")
            elif not val.isdigit():
                add("WARN", label, f"{key} 不是纯数字：{val}")
            else:
                add("OK", label, val)
        paths = cfg.get("paths") or {}
        ffmpeg = Path(str(paths.get("ffmpeg") or ""))
        if paths.get("ffmpeg") and not ffmpeg.exists():
            add("WARN", "ffmpeg", f"配置了但文件不存在：{ffmpeg}")
    if not ACCESS.exists():
        add("WARN", "黑白名单", "没有 access_lists.json，第一次启动会生成空名单")
    else:
        data = read_json(ACCESS)
        users = ((data.get("whitelist") or {}).get("users")) or []
        groups = ((data.get("whitelist") or {}).get("groups")) or []
        if not users and not groups:
            add("WARN", "黑白名单", "白名单是空的，机器人现在谁都不理，去控制台好友页加人")
        else:
            add("OK", "黑白名单", f"白名单 {len(users)} 人 / {len(groups)} 群")


def _numeric_ids(entries) -> set[str]:
    return {
        str(item).strip().rsplit(":", 1)[-1]
        for item in (entries or [])
        if str(item).strip()
    }


def check_access_sync() -> None:
    """插件名单和 AstrBot 自带白名单必须是同一批人，不然会一边拦一边放。"""
    access = read_json(ACCESS)
    cfg = read_json(CMD_CFG)
    wl = access.get("whitelist") or {}
    expected = {str(x).strip() for x in (wl.get("users") or []) if str(x).strip()}
    expected |= {str(x).strip() for x in (wl.get("groups") or []) if str(x).strip()}
    ps = cfg.get("platform_settings") or {}
    actual = _numeric_ids(ps.get("id_whitelist") or [])
    if not expected:
        add("WARN", "名单同步", "插件白名单是空的，跳过")
        return
    if expected == actual:
        add("OK", "名单同步", f"AstrBot 和插件都是 {len(expected)} 个会话")
        return
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    detail = []
    if missing:
        detail.append(f"AstrBot 少 {','.join(missing)}")
    if extra:
        detail.append(f"AstrBot 多 {','.join(extra)}")
    add("WARN", "名单同步", "；".join(detail) + "；去控制台名单页重新保存一次")


def check_persona() -> None:
    if not DB.exists():
        add("WARN", "人设", "还没有 data_v4.db，先启动一次 AstrBot 再跑 import_persona.py")
        return
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
        row = con.execute("select count(*) from personas where persona_id = '大肥鱼'").fetchone()
        con.close()
        if row and row[0]:
            add("OK", "人设", "数据库里已有「大肥鱼」")
        else:
            add("WARN", "人设", "数据库里没有「大肥鱼」，跑 scripts/import_persona.py 或去 6185 建")
    except Exception as exc:  # noqa: BLE001
        add("WARN", "人设", f"查数据库失败：{exc}")


def check_services() -> None:
    for port, label in ((3000, "OneBot HTTP"), (6185, "AstrBot 面板"), (6868, "控制台")):
        if port_open(port):
            add("OK", label, f"127.0.0.1:{port} 在听")
        else:
            add("WARN", label, f"127.0.0.1:{port} 没开")
    snow = sorted((ROOT / "SnowLuma" / "config").glob("onebot_*.json"))
    if snow:
        add("OK", "协议端配置", snow[0].name)
    else:
        add("WARN", "协议端配置", "找不到 SnowLuma/config/onebot_*.json，控制台读不到 token")


def check_panel() -> None:
    if PANEL.exists():
        add("OK", "控制台", str(PANEL))
    else:
        add("FAIL", "控制台", f"找不到 {PANEL}，先跑 install.ps1")


def check_backup() -> None:
    script = ROOT / "backup.ps1"
    if script.exists():
        add("OK", "备份脚本", str(script))
    else:
        add("WARN", "备份脚本", f"找不到 {script}，重跑 install.ps1")
    try:
        r = subprocess.run(
            ["schtasks", "/Query", "/TN", "QQBot-DailyBackup"],
            capture_output=True, timeout=10,
        )
        if r.returncode == 0:
            add("OK", "每日备份任务", "QQBot-DailyBackup 已注册")
        else:
            add("WARN", "每日备份任务", "没有 QQBot-DailyBackup，加 -InstallBackupTask 或手动建任务")
    except Exception as exc:  # noqa: BLE001
        add("WARN", "每日备份任务", f"查询失败：{exc}")
    folder = ROOT / "backups"
    try:
        zips = sorted(folder.glob("qqbot-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        zips = []
    if not zips:
        add("WARN", "最近备份", "还没有备份，控制台点「立即备份」")
        return
    latest = zips[0]
    try:
        age = (time.time() - latest.stat().st_mtime) / 3600
        size = latest.stat().st_size / 1024 / 1024
    except OSError:
        return
    add("OK" if age <= 48 else "WARN", "最近备份", f"{latest.name}，{age:.1f} 小时前，{size:.0f} MB")


def main() -> int:
    print(f"项目根：{ROOT}\n")
    check_astrbot()
    check_patches()
    check_config()
    check_access_sync()
    check_persona()
    check_panel()
    check_backup()
    check_services()

    icon = {"OK": "[OK]  ", "WARN": "[WARN]", "FAIL": "[FAIL]"}
    for level, title, detail in results:
        line = f"{icon[level]} {title}"
        if detail:
            line += f"：{detail}"
        print(line)

    fails = sum(1 for level, _, _ in results if level == "FAIL")
    warns = sum(1 for level, _, _ in results if level == "WARN")
    print(f"\n{len(results)} 项检查：{fails} 个 FAIL，{warns} 个 WARN")
    if fails:
        print("先修 FAIL，再按 docs/V2_TEST.md 验收。")
        return 1
    print("没有硬性错误。可以按 docs/V2_TEST.md 一条条验收了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
