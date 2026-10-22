#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""QQ 重登后的自动恢复。

背景：QQ 客户端重新登录后，SnowLuma 注入的会话会失效，AstrBot 发消息报
「The QQ connection changed. Please restart QQ and try again.」。实测：
只重启 SnowLuma 没用，必须重启 QQ；QQ 重新登录（约 90 秒）后，SnowLuma
会自己重新挂上 Hook 并恢复 OneBot 会话。这个脚本把这一套自动化。

做法：
  1. 每次运行只读 AstrBot 日志里「上次检查之后」新增的部分；
  2. 看到该报错时，先用 OneBot 接口探一下连接是不是真的坏了
     （避免一次偶发报错就把 QQ 重启掉）；
  3. 真坏了 → 重启 QQ → 等会话恢复；还不行 → 再重启 SnowLuma；
  4. 结果写 logs/qq_recover.log，状态写 logs/qq_recover_state.json。

由 watchdog.ps1 每轮调用。手动用法：
  python qq_recover.py            # 检查一次，有错才动
  python qq_recover.py --dry-run  # 只看会不会动，不动手
  python qq_recover.py --force    # 不管有没有报错，强制走一遍恢复流程
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("QQBOT_ROOT") or Path(__file__).resolve().parents[1])
LOGS = ROOT / "logs"
AST_LOG = LOGS / "astrbot.out.log"
SL_OUT = LOGS / "snowluma.out.log"
SL_ERR = LOGS / "snowluma.err.log"
STATE = LOGS / "qq_recover_state.json"
RECOVER_LOG = LOGS / "qq_recover.log"
SNOW = ROOT / "SnowLuma"
QQ_EXE = Path(os.environ.get("QQ_EXE_PATH") or r"D:\Program Files\Tencent\QQNT\QQ.exe")
QQ_DIR = QQ_EXE.parent

# AstrBot 报错原文里出现这些，就认定 QQ 重登把 Hook 弄丢了
PATTERNS = ("the qq connection changed", "qq connection changed")
COOLDOWN = 600        # 一次恢复失败后，至少隔 10 分钟再试
MAX_PER_HOUR = 3      # 一小时内最多恢复 3 次，防止反复重启


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(RECOVER_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------- 状态 ----------------

def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"offset": None, "restarts": []}


def save_state(state: dict) -> None:
    try:
        STATE.write_text(json.dumps(state, ensure_ascii=True), encoding="utf-8")
    except OSError as exc:
        log(f"状态写入失败：{exc}")


def log_size() -> int:
    try:
        return AST_LOG.stat().st_size
    except OSError:
        return 0


def read_since(offset: int) -> tuple[str, int]:
    """读 AstrBot 日志从 offset 到结尾的新内容；日志变小说明轮转了，从头读。"""
    size = log_size()
    if size < offset:
        offset = 0
    try:
        with open(AST_LOG, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return "", offset
    return data.decode("utf-8", "ignore"), size


# ---------------- OneBot 探针 ----------------

def ob_token() -> str:
    for cfg in (SNOW / "config").glob("onebot_*.json"):
        try:
            conf = json.loads(cfg.read_text(encoding="utf-8-sig"))
            return conf["networks"]["httpServers"][0]["accessToken"]
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("读不到 OneBot accessToken")


def onebot(action: str, payload: dict | None = None, timeout: float = 10) -> dict:
    url = f"http://127.0.0.1:3000/{action}"
    headers = {"Authorization": f"Bearer {ob_token()}"}
    if payload is None:
        req = urllib.request.Request(url, headers=headers)
    else:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=body, headers={**headers, "Content-Type": "application/json"}, method="POST"
        )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def probe() -> str:
    """ok / changed / down / error"""
    try:
        res = onebot("get_friend_list", {"no_cache": True})
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), ConnectionRefusedError):
            return "down"
        return "down" if "10061" in str(exc) else "error"
    except Exception:  # noqa: BLE001
        return "error"
    if res.get("status") == "ok":
        return "ok"
    if "connection changed" in str(res.get("wording", "")).lower():
        return "changed"
    return "error"


def wait_connection(timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if probe() == "ok":
            return True
        time.sleep(3)
    return False


# ---------------- 进程与端口 ----------------

def port_open(port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout):
            return True
    except OSError:
        return False


def qq_mains() -> list:
    """QQ 主进程：命令行里没有 --type= 的那个，子进程都挂在它下面。"""
    import psutil

    out = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if (p.info["name"] or "").lower() != "qq.exe":
                continue
            if "--type=" in " ".join(p.info["cmdline"] or []):
                continue
            out.append(p)
        except Exception:  # noqa: BLE001
            continue
    return out


def snowluma_procs() -> list:
    import psutil

    out = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if (p.info["name"] or "").lower() != "node.exe":
                continue
            if "snowluma" in " ".join(p.info["cmdline"] or []).lower():
                out.append(p)
        except Exception:  # noqa: BLE001
            continue
    return out


def rotate(name: str) -> None:
    """和 start.ps1 一致：旧日志改名 .prev.log，留一份好排查。"""
    path = LOGS / name
    try:
        if path.exists() and path.stat().st_size > 0:
            os.replace(path, LOGS / name.replace(".log", ".prev.log"))
    except OSError:
        pass


# ---------------- 重启动作 ----------------

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def spawn(cmd: list[str], cwd: Path, stdout=None, stderr=None, env=None) -> None:
    """起独立进程：隐藏控制台（不然桌面上会多一个终端窗口），并尽量脱离父进程的 Job。"""
    base = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(
            cmd, cwd=str(cwd), stdout=stdout, stderr=stderr, env=env,
            creationflags=base | CREATE_BREAKAWAY_FROM_JOB, close_fds=True,
        )
    except OSError:
        subprocess.Popen(
            cmd, cwd=str(cwd), stdout=stdout, stderr=stderr, env=env,
            creationflags=base, close_fds=True,
        )


def start_qq() -> bool:
    if not QQ_EXE.exists():
        log(f"找不到 QQ：{QQ_EXE}")
        return False
    try:
        spawn([str(QQ_EXE)], QQ_DIR)
        return True
    except OSError as exc:
        log(f"QQ 拉不起来：{exc}")
        return False


def restart_qq(timeout: int = 90) -> tuple[bool, str]:
    """重启 QQ 主进程，等新的主进程出现。返回 (是否成功, 说明)。"""
    olds = qq_mains()
    old_pids = {p.pid for p in olds}
    for p in olds:
        try:
            p.terminate()
        except Exception:  # noqa: BLE001
            pass
    deadline = time.time() + 20
    while time.time() < deadline and qq_mains():
        time.sleep(0.5)
    for p in qq_mains():
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass

    if not start_qq():
        return False, "QQ 启动失败"

    deadline = time.time() + timeout
    while time.time() < deadline:
        now = {p.pid for p in qq_mains()} - old_pids
        if now:
            return True, f"QQ 新进程 PID={sorted(now)[0]}"
        time.sleep(1)
    return False, f"{timeout} 秒内没等到 QQ 新进程"


def restart_snowluma() -> tuple[bool, str]:
    rotate("snowluma.out.log")
    rotate("snowluma.err.log")

    for p in snowluma_procs():
        try:
            p.terminate()
        except Exception:  # noqa: BLE001
            pass
    deadline = time.time() + 15
    while time.time() < deadline and snowluma_procs():
        time.sleep(0.3)
    for p in snowluma_procs():
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass

    deadline = time.time() + 20
    while time.time() < deadline and (port_open(3000) or port_open(5099)):
        time.sleep(0.3)

    env = os.environ.copy()
    env["SNOWLUMA_HOOK_AUTOLOAD"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PATH"] = str(ROOT / "bin") + ";" + env.get("PATH", "")

    try:
        out = open(SL_OUT, "ab")
        err = open(SL_ERR, "ab")
    except OSError as exc:
        return False, f"打不开日志：{exc}"

    try:
        spawn([str(SNOW / "node.exe"), "index.mjs"], SNOW, stdout=out, stderr=err, env=env)
    except OSError as exc:
        return False, f"拉不起来：{exc}"

    deadline = time.time() + 90
    while time.time() < deadline and not port_open(3000):
        time.sleep(1)
    if not port_open(3000):
        return False, "90 秒内 3000 没起来"
    return True, "SnowLuma 已重启"


def recover() -> tuple[bool, str]:
    """完整恢复：重启 QQ → 等会话 → 不行再重启 SnowLuma。

    前提是 QQ 本来就在运行。用户主动退了 QQ 时不要偷偷把它拉起来：
    那种情况等他重新登录，SnowLuma 会自己接上。
    """
    if not qq_mains():
        return False, "QQ 没在运行，不主动拉起（登录后 SnowLuma 会自己接上）"

    ok, msg = restart_qq()
    if not ok:
        return False, msg
    log(msg + "，等它登录")
    if wait_connection(180):
        return True, "重启 QQ 后连接恢复"
    log("QQ 重启后还没恢复，再重启一次 SnowLuma")
    ok, msg = restart_snowluma()
    if not ok:
        return False, msg
    if wait_connection(90):
        return True, "重启 QQ + SnowLuma 后连接恢复"
    return False, "两轮都没恢复，需要人工看"


# ---------------- 主流程 ----------------

def main(argv: list[str]) -> int:
    force = "--force" in argv
    dry = "--dry-run" in argv

    if "--restart-snowluma" in argv:  # 排障用：只重启 SnowLuma，不动 QQ
        ok, detail = restart_snowluma()
        log(f"SnowLuma 重启：{detail}")
        return 0 if ok else 1

    state = load_state()
    if state.get("offset") is None:
        # 第一次跑：只看以后的新日志，避免拿历史错误误重启
        state["offset"] = log_size()
        save_state(state)
        log(f"首次运行，从当前位置开始看（offset={state['offset']}）")
        return 0

    text, new_offset = read_since(int(state["offset"]))
    hit = any(p in text.lower() for p in PATTERNS)

    if dry:
        print(f"dry-run: 新日志 {len(text)} 字，命中={hit}，连接={probe()}")
        return 0
    if not hit and not force:
        state["offset"] = new_offset
        save_state(state)
        return 0

    # SnowLuma 整个没起来是 watchdog 的活，不在这里抢
    if not port_open(5099):
        log("SnowLuma 没在运行 —— 交给 start.ps1，这里不动")
        state["offset"] = new_offset
        save_state(state)
        return 0

    current = probe()
    if current == "ok" and not force:
        log("日志里有连接报错，但探针显示现在是好的，不重启")
        state["offset"] = new_offset
        save_state(state)
        return 0
    if not qq_mains() and not force:
        # 用户自己退了 QQ：别偷偷拉起，等他登录后 SnowLuma 会自己接上
        log("QQ 没在运行，先不动；登录后 SnowLuma 会自己接上")
        state["offset"] = new_offset
        save_state(state)
        return 0
    if current in ("down", "error") and not force:
        log(f"日志里有连接报错，但探针结果={current}，先不动，等下一轮")
        return 0
    if current == "changed" and not force:
        # 再确认一次，别因为一瞬间的抖动就把 QQ 重启掉
        time.sleep(5)
        again = probe()
        if again == "ok":
            log("第一次探针报断，5 秒后恢复正常，不重启")
            state["offset"] = new_offset
            save_state(state)
            return 0
        if again != "changed":
            log(f"复核探针结果={again}，先不动，等下一轮")
            return 0

    now = time.time()
    recent = [t for t in state.get("restarts", []) if now - t < 3600]
    if recent and now - recent[-1] < COOLDOWN:
        log(f"上次恢复在 {int(now - recent[-1])} 秒前，先等冷却")
        return 0
    if len(recent) >= MAX_PER_HOUR:
        log(f"一小时已恢复 {len(recent)} 次，暂停自动恢复，等人工看")
        return 0

    reason = "手动强制" if force and not hit else "检测到 QQ 连接变了"
    log(f"{reason}，开始恢复")
    ok, detail = recover()
    recent.append(now)
    state["restarts"] = recent[-MAX_PER_HOUR:]
    if ok:
        state["offset"] = log_size()  # 这条错误已经处理，别再重复触发
        log(f"恢复成功：{detail}")
    else:
        log(f"恢复失败：{detail}（下一轮再试）")
    save_state(state)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
