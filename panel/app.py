"""大肥鱼控制台 v2

在 v1 基础上补齐成熟面板常见能力：
- 实时消息流（解析 SnowLuma 日志）
- 发送测试（文本 / 音乐卡片 / 表情包）
- 会话管理（查看 + 清空上下文）
- 插件开关（启用 / 停用）
- 群友列表（群 / 好友 / 群成员）
- 配置查看与编辑（自动备份）

只监听 127.0.0.1。
"""

import base64
import contextlib
import datetime
import html
import json
import os
import pathlib
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

# 项目根目录：优先读环境变量 QQBOT_ROOT，否则用本文件所在目录的上一级
ROOT = pathlib.Path(
    os.environ.get("QQBOT_ROOT") or pathlib.Path(__file__).resolve().parent.parent
).resolve()

# pythonw 没有标准输出，uvicorn 写日志会崩，这里兜底重定向到文件
_stdio_fallback = None
if sys.stdout is None or sys.stderr is None:
    try:
        (ROOT / "logs").mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    _stdio_fallback = open(ROOT / "logs" / "panel.console.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _stdio_fallback
    sys.stderr = sys.stderr or _stdio_fallback

import jwt
import random
import requests
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

LOG_DIR = ROOT / "logs"
DATA = ROOT / "AstrBot" / "data"
CFG = DATA / "cmd_config.json"
BOT_CONFIG = ROOT / "data" / "dafeiyu" / "config.json"
DB = DATA / "data_v4.db"
STICKERS = ROOT / "stickers"
PLUGINS = DATA / "plugins"
DISABLED = ROOT / "disabled_plugins"
BACKUP_DIR = ROOT / "backups"
BACKUP_STATE = ROOT / "panel" / "backup_state.json"
BACKUP_CONFIG = ROOT / "data" / "dafeiyu" / "backup_config.json"
BACKUP_DEFAULTS = {
    "astrbot_data": True,
    "dafeiyu_data": True,
    "snowluma_config": True,
    "secrets": True,
    "personas": True,
    "panel_code": True,
    "panel_state": True,
    "rvc_map": True,
    "backup_script": True,
    "stickers": False,
    "retention": 14,
}


def _find_snow_cfg() -> pathlib.Path:
    """雪花/协议端配置：优先环境变量，其次自动找 SnowLuma/config/onebot_*.json。"""
    env = os.environ.get("SNOWLUMA_CONFIG")
    if env:
        return pathlib.Path(env)
    cfg_dir = ROOT / "SnowLuma" / "config"
    files = sorted(cfg_dir.glob("onebot_*.json"))
    return files[0] if files else cfg_dir / "onebot.json"


SNOW_CFG = _find_snow_cfg()
_ROOT_PS = str(ROOT).replace("'", "''")


def _bot_cfg() -> dict:
    """读统一配置 data/dafeiyu/config.json，读不到就当空。"""
    try:
        return json.loads(BOT_CONFIG.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _cfg_str(key: str) -> str:
    """配置优先，其次 DAFEIYU_<KEY> 环境变量。"""
    val = _bot_cfg().get(key)
    if val in (None, ""):
        val = os.environ.get(f"DAFEIYU_{key.upper()}", "")
    return str(val or "").strip()

# 音色模型根目录（用户自己往里放模型）
MODEL_ROOT = pathlib.Path(os.environ.get("GPT_MODEL_ROOT") or ROOT / "models" / "tts")
MODEL_STATE = ROOT / "panel" / "model_state.json"
RVC_ROOT = ROOT / "rvc"
RVC_MODELS = RVC_ROOT / "models"
RVC_STATE = ROOT / "panel" / "rvc_state.json"
SING_DIR = ROOT / "panel" / "sing_test"
VOICE_CFG = DATA / "config" / "astrbot_plugin_dafeiyu_voice_config.json"
EMOTION_DIR_NAMES = (
    "happy", "excited", "angry", "sad", "shy", "coquettish", "confused", "grievance", "surprised", "neutral",
    "开心", "激动", "生气", "难过", "害羞", "撒娇", "疑惑", "委屈", "惊讶", "中立",
)
IMG_DIR = ROOT / "logs" / "panel_images"
IMG_INDEX = IMG_DIR / "index.jsonl"
PY = str(ROOT / "AstrBot" / ".venv" / "Scripts" / "python.exe")

# 网易云账号（本地 cookie），供「音乐」页用
sys.path.insert(0, str(ROOT / "scripts"))
try:
    import netease  # noqa: E402
except Exception:  # noqa: BLE001
    netease = None

NETEASE_TARGET = ROOT / "data" / "netease_target.json"

app = FastAPI(title="大肥鱼控制台")


# ---------------- 工具 ----------------
def port_open(port: int, timeout: float = 0.15) -> bool:
    with socket.socket() as s:
        s.settimeout(timeout)
        return s.connect_ex(("127.0.0.1", port)) == 0


def read_cfg() -> dict:
    return json.loads(CFG.read_text(encoding="utf-8-sig"))


def write_cfg(cfg: dict) -> None:
    # 不带 BOM：带 BOM 会让一些读 json 的程序报错
    CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def ob_token() -> str:
    conf = json.loads(SNOW_CFG.read_text(encoding="utf-8-sig"))
    return conf["networks"]["httpServers"][0]["accessToken"]


def ob_call(api: str, payload: dict | None = None, timeout: float = 8) -> dict:
    headers = {"Authorization": f"Bearer {ob_token()}"}
    try:
        if payload is None:
            r = requests.get(f"http://127.0.0.1:3000/{api}", headers=headers, timeout=timeout)
        else:
            r = requests.post(
                f"http://127.0.0.1:3000/{api}", headers=headers, json=payload, timeout=timeout
            )
        return r.json()
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "wording": str(exc)}


_login_cache = {"ts": 0.0, "data": {"status": "unknown"}, "ok": None}


def _bot_qq() -> str:
    """机器人号，从协议端配置文件名里取，比如 onebot_1234567890.json。"""
    m = re.search(r"onebot_(\d+)", SNOW_CFG.name)
    return m.group(1) if m else ""


def _login_worker() -> None:
    """持续盯 OneBot：服务不在、或登录的号不是机器人号，都算掉线。1 秒一轮。"""
    bot = _bot_qq()
    while True:
        if not port_open(3000, 0.2):
            # QQ 一退，SnowLuma 会把 OneBot 服务停掉，这里最快
            _login_cache["data"] = {"status": "failed", "wording": "OneBot 服务未就绪"}
            _login_cache["ok"] = False
            _login_cache["ts"] = time.time()
            time.sleep(1)
            continue
        try:
            d = ob_call("get_login_info", timeout=1.5)
        except Exception:  # noqa: BLE001
            d = {"status": "failed"}
        uid = str((d.get("data") or {}).get("user_id") or "")
        good = d.get("status") == "ok" and (not bot or uid == bot)
        _login_cache["data"] = d if good else {"status": "failed", "wording": "登录的不是机器人号"}
        _login_cache["ok"] = good
        _login_cache["ts"] = time.time()
        time.sleep(1)


def login_cached() -> dict:
    return _login_cache["data"]


def login_ok() -> bool:
    return bool(_login_cache.get("ok"))


threading.Thread(target=_login_worker, daemon=True).start()


def tail(path: pathlib.Path, lines: int = 200) -> str:
    if not path.exists():
        return "（日志不存在）"
    try:
        data = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:  # noqa: BLE001
        return f"读取失败: {exc}"
    return "\n".join(data[-lines:])


def run_bg(args: list[str]) -> None:
    subprocess.Popen(
        args, cwd=str(ROOT), stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=0x08000000,
    )


# ---------------- 备份 ----------------
_BACKUP_LOCK = threading.Lock()
_BACKUP_THREAD: threading.Thread | None = None


def _backup_items(limit: int = 20) -> list[dict]:
    try:
        files = sorted(
            BACKUP_DIR.glob("qqbot-*.zip"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []
    items = []
    for path in files[:limit]:
        try:
            st = path.stat()
        except OSError:
            continue
        items.append({"name": path.name, "size": st.st_size, "mtime": st.st_mtime})
    return items


def _backup_count() -> int:
    try:
        return len([
            p for p in BACKUP_DIR.glob("qqbot-*.zip")
            if not p.name.startswith("qqbot-deploy-")
        ])
    except OSError:
        return 0


def _backup_state_read() -> dict:
    try:
        data = json.loads(BACKUP_STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _backup_state_write(state: dict) -> None:
    try:
        BACKUP_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = BACKUP_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(BACKUP_STATE)
    except OSError:
        pass


def _backup_config_read() -> dict:
    data = {}
    try:
        raw = json.loads(BACKUP_CONFIG.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data = raw
    except Exception:  # noqa: BLE001
        data = {}
    out = {}
    for key, default in BACKUP_DEFAULTS.items():
        if key == "retention":
            try:
                out[key] = max(1, min(60, int(data.get(key, default))))
            except (TypeError, ValueError):
                out[key] = default
        else:
            out[key] = bool(data.get(key, default))
    return out


def _backup_config_write(cfg: dict) -> dict:
    clean = _backup_config_read()
    for key in BACKUP_DEFAULTS:
        if key == "retention":
            try:
                clean[key] = max(1, min(60, int(cfg.get(key, clean[key]))))
            except (TypeError, ValueError):
                pass
        elif key in cfg:
            clean[key] = bool(cfg[key])
    BACKUP_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = BACKUP_CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(BACKUP_CONFIG)
    return clean


def _backup_run() -> None:
    started = time.time()
    state = {
        "running": True,
        "started_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": "",
        "ok": None,
        "zip": "",
        "size": 0,
        "error": "",
    }
    _backup_state_write(state)
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(ROOT / "backup.ps1")],
            cwd=str(ROOT),
            capture_output=True,
            timeout=1800,
            creationflags=0x08000000,
        )
        if result.returncode != 0:
            detail = ((result.stderr or b"") + (result.stdout or b"")).decode(
                "utf-8", "replace"
            )[-500:]
            raise RuntimeError(detail.strip() or f"backup.ps1 退出码 {result.returncode}")
        fresh = []
        for path in BACKUP_DIR.glob("qqbot-*.zip"):
            try:
                if path.stat().st_mtime >= started - 5:
                    fresh.append(path)
            except OSError:
                continue
        if not fresh:
            raise RuntimeError("没有生成新的备份文件")
        latest = max(fresh, key=lambda p: p.stat().st_mtime)
        state.update({"ok": True, "zip": latest.name, "size": latest.stat().st_size})
    except Exception as exc:  # noqa: BLE001
        state.update({"ok": False, "error": str(exc)[-500:]})
    finally:
        state["running"] = False
        state["finished_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _backup_state_write(state)


def _start_backup() -> bool:
    global _BACKUP_THREAD
    with _BACKUP_LOCK:
        if _BACKUP_THREAD is not None and _BACKUP_THREAD.is_alive():
            return False
        _BACKUP_THREAD = threading.Thread(
            target=_backup_run, name="dafeiyu-backup", daemon=True
        )
        _BACKUP_THREAD.start()
        return True


def _backup_status() -> dict:
    state = _backup_state_read()
    items = _backup_items(20)
    running = bool(_BACKUP_THREAD is not None and _BACKUP_THREAD.is_alive())
    return {
        "running": running,
        "ok": state.get("ok"),
        "error": state.get("error", ""),
        "started_at": state.get("started_at", ""),
        "finished_at": state.get("finished_at", ""),
        "latest": items[0] if items else None,
        "count": _backup_count(),
        "items": items[:5],
        "config": _backup_config_read(),
    }


# ---------------- 模块管理 ----------------
MODULE_CATALOG = [
    ("access", "名单守门", "黑白名单检查：白名单外的人和群不理，黑名单优先。"),
    ("asr", "语音转文字", "收到语音先用本机 faster-whisper 转成文字，主模型不认音频也不会整条挂掉。"),
    ("card", "音乐卡片", "解析点歌插件发的音乐卡片。"),
    ("face", "表情包", "按情绪从表情文件夹发图，也会把群里的图收进素材库。"),
    ("maintenance_guard", "维护模式", "维护时压住正常回复，由插件直接回一句维护提示。"),
    ("memory_scheduler", "自动记忆", "群聊攒够 25 条或每 30 分钟，把上下文整理进个人档案。"),
    ("oneline", "回复收成一条", "把回复里的换行收掉，QQ 里看到一条完整的话。"),
    ("profile", "人物档案", "注入当前说话人、喜好、关系和群名单；记忆的统一入口。"),
    ("qzone", "QQ 空间", "AI 说说、草稿和每日日记。"),
    ("reply_gate", "接话判断", "群聊里先判断这句要不要接，不是每句都回。"),
    ("sendto", "跨群发送", "让机器人把一句话发到别的群或私聊。"),
    ("stream", "消息流", "把收发消息写进 panel_stream.jsonl，控制台和记忆都靠它。"),
    ("voice", "语音和唱歌", "按情绪调 GPT-SoVITS 发语音，RVC 唱歌。"),
]
_MODULE_NAMES = {name for name, _, _ in MODULE_CATALOG}


def _module_runtime() -> dict[str, bool]:
    """从 AstrBot 日志看每个模块最后一次是加载还是关闭。"""
    text = tail(LOG_DIR / "astrbot.out.log", 5000)
    state: dict[str, bool] = {}
    for line in text.splitlines():
        match = re.search(r"\[dafeiyu\] 模块(加载|关闭)：([A-Za-z0-9_]+)", line)
        if match:
            state[match.group(2)] = match.group(1) == "加载"
    return state


def _module_row(name: str, label: str, desc: str, enabled: bool, live: bool | None, astrbot_up: bool) -> dict:
    if not astrbot_up:
        status = "AstrBot 未运行"
    elif live is None:
        status = "已启用" if enabled else "已停用"
    elif enabled and live:
        status = "运行中"
    elif enabled and not live:
        status = "已启用，重启后加载"
    elif not enabled and live:
        status = "运行中，重启后停用"
    else:
        status = "已停用"
    return {
        "name": name,
        "label": label,
        "desc": desc,
        "enabled": enabled,
        "running": bool(live) and astrbot_up,
        "status": status,
        "restart_required": bool(astrbot_up and live is not None and live != enabled),
    }


@app.get("/api/modules")
def api_modules() -> JSONResponse:
    cfg = _bot_cfg()
    mods = cfg.get("modules") or {}
    runtime = _module_runtime()
    astrbot_up = port_open(6185)
    rows = []
    for name, label, desc in MODULE_CATALOG:
        section = mods.get(name) or {}
        enabled = bool(section.get("enabled", True))
        rows.append(_module_row(name, label, desc, enabled, runtime.get(name), astrbot_up))
    return JSONResponse({
        "modules": rows,
        "astrbot": astrbot_up,
        "config": str(BOT_CONFIG),
    })


class ModuleToggleIn(BaseModel):
    name: str
    enabled: bool


@app.post("/api/modules/toggle")
def api_module_toggle(payload: ModuleToggleIn) -> JSONResponse:
    if payload.name not in _MODULE_NAMES:
        raise HTTPException(400, "unknown module")
    cfg = _bot_cfg()
    modules = cfg.setdefault("modules", {})
    section = modules.setdefault(payload.name, {})
    section["enabled"] = bool(payload.enabled)
    BOT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    BOT_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return JSONResponse({
        "ok": True,
        "name": payload.name,
        "enabled": bool(payload.enabled),
        "msg": "已保存，重启 AstrBot 后生效",
    })


# ---------------- 体检和修复 ----------------
def _doctor_text() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [PY, str(ROOT / "scripts" / "doctor.py")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            creationflags=0x08000000,
        )
        return result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return False, f"体检没跑起来：{exc}"


@app.get("/api/doctor")
def api_doctor() -> JSONResponse:
    ok, text = _doctor_text()
    return JSONResponse({"ok": ok, "text": text})


@app.post("/api/repair/patches")
def api_repair_patches() -> JSONResponse:
    script = ROOT / "patches" / "apply_core_patches.ps1"
    if not script.exists():
        return JSONResponse({"ok": False, "msg": f"找不到 {script}"})
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(script), "-AstrBotRoot", str(ROOT / "AstrBot")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            creationflags=0x08000000,
        )
        return JSONResponse({
            "ok": result.returncode == 0,
            "msg": "补丁已打，重启 AstrBot 生效",
            "text": ((result.stdout or "") + (result.stderr or ""))[-2000:],
        })
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "msg": f"打补丁失败：{exc}"})


@app.post("/api/repair/asr")
def api_repair_asr() -> JSONResponse:
    script = ROOT / "scripts" / "fetch_asr_model.py"
    if not script.exists():
        return JSONResponse({"ok": False, "msg": f"找不到 {script}"})
    run_bg([PY, str(script)])
    return JSONResponse({"ok": True, "msg": "语音模型下载已在后台开始"})


def restart_astrbot() -> None:
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         f"Where-Object {{ $_.ExecutablePath -like '{_ROOT_PS}\\AstrBot\\*' }} | "
         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
        capture_output=True, timeout=30, creationflags=0x08000000,
    )
    time.sleep(2)
    run_bg(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(ROOT / "start.ps1")])


def restart_snowluma() -> None:
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
         f"Where-Object {{ $_.ExecutablePath -like '{_ROOT_PS}\\SnowLuma\\*' }} | "
         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
        capture_output=True, timeout=30, creationflags=0x08000000,
    )
    time.sleep(2)
    run_bg(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(ROOT / "start.ps1")])


# ---------------- 状态 ----------------
@app.get("/api/status")
def status() -> JSONResponse:
    cfg = read_cfg()
    ps = cfg.get("platform_settings", {})
    ltm = cfg.get("provider_ltm_settings", {})
    comp = cfg["agent_runner"]["config"]["compression"]

    plugins = sorted(d.name for d in PLUGINS.iterdir() if d.is_dir()) if PLUGINS.exists() else []
    disabled = sorted(d.name for d in DISABLED.iterdir() if d.is_dir()) if DISABLED.exists() else []

    login = login_cached()

    return JSONResponse({
        "ports": {
            "onebot_http": port_open(3000), "onebot_ws": port_open(3001),
            "snowluma": port_open(5099), "astrbot": port_open(6185),
            "reverse_ws": port_open(6199), "panel": True,
        },
        "login": login.get("data", {}).get("nickname") if login.get("status") == "ok" else None,
        "reply_all": cfg.get("wake_prefix") == [""],
        "segmented": ps.get("segmented_reply", {}).get("enable"),
        "ignore_self": ps.get("ignore_bot_self_message"),
        "whitelist": ps.get("id_whitelist", []),
        "admins": cfg.get("admins_id", []),
        "max_turns": comp.get("max_turns"),
        "group_icl": ltm.get("group_icl_enable"),
        "web_search": cfg.get("provider_settings", {}).get("web_search"),
        "plugins_detail": [_plugin_meta(n) for n in plugins],
        "disabled_detail": [_plugin_meta(n) for n in disabled],
        "backups": len(list((ROOT / "backups").glob("qqbot-*.zip"))),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    })



def _load_img_index() -> list[dict]:
    if not IMG_INDEX.exists():
        return []
    rows = []
    for line in IMG_INDEX.read_text(encoding="utf-8").splitlines()[-300:]:
        try:
            rows.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return rows


def _match_image(hhmmss: str, is_image: bool) -> str | None:
    """按日志时间就近匹配一张已采集的图片。"""
    if not is_image:
        return None
    rows = _load_img_index()
    if not rows:
        return None
    try:
        h, m, s = (int(x) for x in hhmmss.split(":"))
    except Exception:  # noqa: BLE001
        return None
    now = time.localtime()
    target = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, h, m, s, 0, 0, -1))
    best, best_gap = None, 999
    for r in rows[-60:]:
        gap = abs(float(r.get("ts", 0)) - target)
        if gap < best_gap:
            best, best_gap = r, gap
    return best.get("file") if best and best_gap <= 20 else None


# ---------------- QQ 图片缓存兜底 ----------------
def _documents_dir() -> pathlib.Path:
    """Windows 的「文档」可能在 D 盘，读注册表比 ~/Documents 准。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "Personal")
        return pathlib.Path(os.path.expandvars(value))
    except Exception:  # noqa: BLE001
        return pathlib.Path(os.path.expanduser("~")) / "Documents"


def _qq_pic_roots() -> list[pathlib.Path]:
    """自动找 QQ 的图片缓存目录；也可以用 QQBOT_QQ_PIC_DIRS 手动指定（分号分隔）。"""
    env = os.environ.get("QQBOT_QQ_PIC_DIRS")
    if env:
        return [pathlib.Path(p) for p in env.split(os.pathsep) if p.strip()]
    base = _documents_dir() / "Tencent Files"
    return sorted(base.glob("*/nt_qq/nt_data/Pic"))


_qq_cache = {"ts": 0.0, "files": []}


def _qq_cache_files():
    now = time.time()
    if now - _qq_cache["ts"] < 30 and _qq_cache["files"]:
        return _qq_cache["files"]
    files = []
    for root in _qq_pic_roots():
        if not root.exists():
            continue
        for f in root.glob("*/Ori/*"):
            if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                try:
                    files.append((f, f.stat().st_mtime))
                except Exception:
                    continue
    files.sort(key=lambda x: x[1], reverse=True)
    _qq_cache["ts"] = now
    _qq_cache["files"] = files[:400]
    return _qq_cache["files"]


def _fill_from_qq_cache(rows):
    pending = [r for r in rows if r.get("is_image") and not r.get("image")]
    if not pending:
        return
    files = list(_qq_cache_files())
    used = {r["image"] for r in rows if r.get("image")}
    now = time.localtime()
    for r in pending:
        try:
            h, m, sec = (int(x) for x in r["time"].split(":"))
        except Exception:
            continue
        target = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, h, m, sec, 0, 0, -1))
        best, best_gap = None, 1e9
        for f, mt in files:
            if f.name in used:
                continue
            gap = abs(mt - target)
            if gap < best_gap:
                best, best_gap = f, gap
        if best is not None and best_gap <= 180:
            used.add(best.name)
            r["image"] = best.name
            r["image_src"] = "qq"



_contacts_cache = {"ts": 0.0, "groups": {}, "friends": {}}


def _contacts() -> dict:
    now = time.time()
    if now - _contacts_cache["ts"] < 600 and (_contacts_cache["groups"] or _contacts_cache["friends"]):
        return _contacts_cache
    groups, friends = {}, {}
    try:
        g = ob_call("get_group_list").get("data") or []
        groups = {str(x.get("group_id")): x.get("group_name") or "" for x in g}
    except Exception:  # noqa: BLE001
        pass
    try:
        f = ob_call("get_friend_list").get("data") or []
        friends = {str(x.get("user_id")): x.get("nickname") or "" for x in f}
    except Exception:  # noqa: BLE001
        pass
    _contacts_cache.update({"ts": now, "groups": groups, "friends": friends})
    return _contacts_cache



_PLUGIN_NAMES = {
    "astrbot_plugin_dafeiyu_face": "表情包",
    "astrbot_plugin_mingyue_wake": "关键词唤醒",
    "astrbot_plugin_memorix": "长期记忆",
    "astrbot_plugin_music": "点歌",
    "astrbot_plugin_jrysprpr": "今日运势",
    "astrbot_plugin_angel_memory": "天使记忆",
}

# 控制台统一用人话介绍每个插件，覆盖插件自带的英文/技术描述
_PLUGIN_DESC = {
    "astrbot_plugin_dafeiyu_face": "模型判断情绪后自己发图，也把收到的图存一份给消息流",
    "astrbot_plugin_dafeiyu_stream": "把收发消息记下来，控制台消息流靠它",
    "astrbot_plugin_dafeiyu_voice": "用角色声音说话和唱歌，发不发、什么情绪都由模型决定",
    "astrbot_plugin_help": "/helps 出一张命令菜单图",
    "astrbot_plugin_llm_amnesia": "说错话了用 /forget 让它忘掉重来",
    "astrbot_plugin_music": "点歌、出卡片、查歌词，音乐群的核心",
    "astrbot_plugin_pokepro": "被戳头像时会反戳或者回你一句",
    "astrbot_plugin_proactive_chat": "群里冷场时自己找个话题开口",
    "astrbot_plugin_qqadmin": "禁言、踢人、违禁词、广告拦截",
    "astrbot_plugin_dafeiyu_mod": "自写的群规审核（已停用）",
    "astrbot_plugin_memorix": "自写的记忆插件（已停用）",
    "astrbot_plugin_angel_memory": "天使记忆（已停用）",
}


def _plugin_meta(dir_name: str) -> dict:
    meta = PLUGINS / dir_name / "metadata.yaml"
    info = {"dir": dir_name, "display": _PLUGIN_NAMES.get(dir_name, dir_name),
            "desc": "", "version": ""}
    if meta.exists():
        try:
            import yaml

            data = yaml.safe_load(meta.read_text(encoding="utf-8")) or {}
            info["display"] = data.get("display_name") or info["display"]
            info["desc"] = _PLUGIN_DESC.get(dir_name) or (data.get("desc") or "").split("\n")[0][:80]
            info["version"] = str(data.get("version") or "")
        except Exception:  # noqa: BLE001
            pass
    if not info["desc"]:
        info["desc"] = _PLUGIN_DESC.get(dir_name, "")
    if dir_name in _PLUGIN_NAMES:
        info["display"] = _PLUGIN_NAMES[dir_name]
    return info



STREAM_FILE = LOG_DIR / "panel_stream.jsonl"


def _stream_rows(limit: int = 400) -> list[dict]:
    """读取插件记录的完整消息流（不截断）。"""
    if not STREAM_FILE.exists():
        return []
    rows: list[dict] = []
    try:
        lines = STREAM_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-limit * 2:]
    except Exception:  # noqa: BLE001
        return []
    c = _contacts()
    img_idx = _load_img_index()
    for line in lines:
        try:
            d = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        ts = float(d.get("ts") or 0)
        cid = str(d.get("conv_id") or "")
        if d.get("is_group"):
            where, conv = "群聊", (c["groups"].get(cid) or "本群")
        else:
            where, conv = "私聊", (c["friends"].get(cid) or d.get("sender") or "私聊")
        txt = d.get("text") or ""
        imgs = int(d.get("images") or 0)
        image = None
        if imgs and img_idx:
            best, gap = None, 1e9
            for it in img_idx[-80:]:
                g = abs(float(it.get("ts", 0)) - ts)
                if g < gap:
                    best, gap = it, g
            if best and gap <= 15:
                image = best.get("file")
        rows.append({
            "time": time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "--:--:--",
            "dir": d.get("dir") or "in",
            "where": where,
            "conv": conv,
            "sender": d.get("sender") or ("大肥鱼" if d.get("dir") == "out" else "群友"),
            "sender_id": d.get("uid") or "",
            "text": txt or ("[图片]" * imgs if imgs else ""),
            "is_image": bool(imgs),
            "image": image,
            "_ts": ts,
        })
    return rows[-limit:]


# ---------------- 消息流 ----------------
def _secs(hhmmss: str) -> int | None:
    try:
        h, m, sec = (int(x) for x in hhmmss.split(":"))
        return h * 3600 + m * 60 + sec
    except Exception:  # noqa: BLE001
        return None


@app.get("/api/messages")
def messages(lines: int = 120, order: str = "desc") -> JSONResponse:
    path = LOG_DIR / "snowluma.out.log"
    if not path.exists():
        return JSONResponse([])
    raw = path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines * 6:]
    out = []
    ev = re.compile(
        r"^(\d{2}:\d{2}:\d{2})\s+\S+\s+\[\d+\]\s+\[Event\]\s+(\[自身\]\s+)?"
        r"(私聊|群)\s+\[(.+?)\((\d+)\)\]"
        r"(?:\s*\|\s*\[(.*?)(?:\((\d+)\))?\]:)?.*?ID:\S+\s*(.*)$"
    )
    send = re.compile(
        r"^(\d{2}:\d{2}:\d{2})\s+\S+\s+(?:\[\d+\]\s+)?\[OneBot\]\s+"
        r"(私聊|群聊)\s+(\d+)\s*\|\s*发送：(.*)$"
    )
    stamp = re.compile(r"^\d{2}:\d{2}:\d{2}\s")

    def gather(start_idx: int, first: str) -> tuple[str, int]:
        """把没有时间戳前缀的后续行（多行消息）拼回同一条。"""
        parts = [first]
        j = start_idx + 1
        while j < len(raw):
            nxt = raw[j]
            if stamp.match(nxt) or " [Event] " in nxt or "[OneBot]" in nxt:
                break
            if nxt.strip():
                parts.append(nxt.rstrip())
            j += 1
        return "\n".join(parts).strip()[:4000], j

    i = 0
    while i < len(raw):
        line = raw[i]
        m = ev.match(line)
        if m:
            t, self_flag, where, conv_name, conv_id, sender, sender_id, first = m.groups()
            txt, nxt_i = gather(i, (first or "").strip())
            i = nxt_i
            if self_flag:
                prev = out[-1] if out else None
                if (
                    prev
                    and prev["dir"] == "out"
                    and _secs(t) is not None
                    and _secs(prev["time"]) is not None
                    and 0 <= _secs(t) - _secs(prev["time"]) <= 3
                ):
                    if "[JSON消息]" in prev["text"] or not prev["text"].strip():
                        prev["text"] = txt
                continue
            out.append({
                "time": t, "dir": "in",
                "where": "私聊" if where == "私聊" else "群聊",
                "conv": conv_name,
                "sender": sender or conv_name,
                "sender_id": sender_id or conv_id,
                "text": txt,
                "is_image": ("图片" in txt),
                "image": _match_image(t, bool("图片" in txt)),
            })
            continue

        m = send.match(line)
        if m:
            t, where, gid, first = m.groups()
            txt, nxt_i = gather(i, (first or "").strip())
            i = nxt_i
            c = _contacts()
            if gid in c["groups"]:
                _where, _conv = "群聊", c["groups"][gid] or gid
            elif gid in c["friends"]:
                _where, _conv = "私聊", c["friends"][gid] or gid
            else:
                _where, _conv = ("私聊" if where == "私聊" else "群聊"), "未知会话"
            out.append({
                "time": t, "dir": "out",
                "where": _where,
                "conv": _conv,
                "sender": "大肥鱼",
                "sender_id": "bot",
                "text": txt,
            })
            continue
        i += 1

    # 先用插件记录的完整流（不截断），日志只补它之前的历史
    stream = _stream_rows(max(lines, 300))
    if stream:
        cutoff = stream[0]["_ts"] - 5
        def _epoch(r):
            sc = _secs(r["time"]) or 0
            now = time.localtime()
            return time.mktime((now.tm_year, now.tm_mon, now.tm_mday,
                                sc // 3600, (sc % 3600) // 60, sc % 60, 0, 0, -1))
        older = [r for r in out if _epoch(r) < cutoff]
        for r in older:
            r["text"] = (r.get("text") or "").strip()
        out = older + stream

    # 先按时间升序，取最近 N 条，再按请求方向返回
    out.sort(key=lambda r: (_secs(r["time"]) or 0) if not r.get("_ts") else r["_ts"])
    out = out[-lines:]
    _fill_from_qq_cache(out)
    if order != "asc":
        out = list(reversed(out))
    return JSONResponse(out)






# ---------------- 语音开关 ----------------
VOICE_CFG = DATA / "config" / "astrbot_plugin_dafeiyu_voice_config.json"
SOVITS_PS1 = ROOT / "start_sovits.ps1"


def _port_open(port: int) -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.6):
            return True
    except Exception:  # noqa: BLE001
        return False


def _voice_cfg() -> dict:
    try:
        return json.loads(VOICE_CFG.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}


def _effective_voice_cfg() -> dict:
    """旧语音配置打底，统一配置覆盖；以插件实际读到的为准。"""
    cfg = dict(_voice_cfg())
    try:
        unified = json.loads(BOT_CONFIG.read_text(encoding="utf-8")).get("modules", {}).get("voice") or {}
        cfg.update({k: v for k, v in unified.items() if v is not None})
    except (OSError, ValueError):
        pass
    return cfg


@app.get("/api/voice")
def voice_state() -> JSONResponse:
    cfg = _effective_voice_cfg()
    running = _port_open(9880)
    return JSONResponse({"cfg": cfg, "sovits_running": running})


class VoiceSetIn(BaseModel):
    key: str
    value: object


@app.post("/api/voice")
def voice_set(payload: VoiceSetIn) -> JSONResponse:
    cfg = _voice_cfg()
    cfg[payload.key] = payload.value
    VOICE_CFG.parent.mkdir(parents=True, exist_ok=True)
    VOICE_CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=4), encoding="utf-8")
    if payload.key == "voice_enable":
        try:
            data = json.loads(BOT_CONFIG.read_text(encoding="utf-8"))
            data.setdefault("modules", {}).setdefault("voice", {})["voice_enable"] = bool(payload.value)
            BOT_CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            pass
    return JSONResponse({"ok": True, "cfg": cfg})


class SovitsIn(BaseModel):
    action: str


@app.post("/api/sovits")
def sovits_ctl(payload: SovitsIn) -> JSONResponse:
    import subprocess

    if payload.action == "stop":
        killed = 0
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*api_v2*' } | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }"],
            capture_output=True, text=True,
        )
        killed = len([x for x in (out.stdout or "").split() if x.strip().isdigit()])
        return JSONResponse({"ok": True, "message": f"已停止（{killed} 个进程）"})
    if payload.action == "start":
        if _port_open(9880):
            return JSONResponse({"ok": True, "message": "已经在运行"})
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SOVITS_PS1)],
            creationflags=0x08000000,
        )
        return JSONResponse({"ok": True, "message": "正在后台启动，约 40 秒后可用"})
    return JSONResponse({"ok": False, "message": "未知操作"})


# ---------------- 插件参数 ----------------
@app.get("/api/plugin_schema")
def plugin_schema(name: str) -> JSONResponse:
    schema = {}
    for sp in (PLUGINS / name / "_conf_schema.json", DISABLED / name / "_conf_schema.json"):
        if sp.exists():
            try:
                schema = json.loads(sp.read_text(encoding="utf-8-sig"))
            except Exception:  # noqa: BLE001
                schema = {}
            break
    cfg_path = DATA / "config" / f"{name}_config.json"
    values: dict = {}
    if cfg_path.exists():
        try:
            values = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            values = {}
    for key, item in (schema or {}).items():
        if key not in values and isinstance(item, dict):
            values[key] = item.get("default")
    meta = _plugin_meta(name)
    return JSONResponse({
        "name": name,
        "display": meta.get("display") or name,
        "schema": schema,
        "values": values,
        "config_path": str(cfg_path),
    })


class PluginConfIn(BaseModel):
    name: str
    values: dict


@app.post("/api/plugin_config")
def plugin_config_save(payload: PluginConfIn) -> JSONResponse:
    cfg_dir = DATA / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / f"{payload.name}_config.json"
    if path.exists():
        backup = cfg_dir / f"{payload.name}_config.bak{int(time.time())}.json"
        shutil.copy2(path, backup)
    path.write_text(json.dumps(payload.values, ensure_ascii=False, indent=4), encoding="utf-8")
    return JSONResponse({"ok": True, "path": str(path),
                         "note": "已保存。冷却/上限等即时生效，关键词改动需要重启 AstrBot"})


# ---------------- 发送测试 ----------------
class SendIn(BaseModel):
    target: str  # friend | group
    kind: str    # text | music | sticker
    text: str = ""
    song_id: int = 0
    mood: str = "happy"
    uin: str = ""  # 不填就读配置里的 admin_qq / group


@app.post("/api/send")
def send(payload: SendIn) -> JSONResponse:
    uid = (payload.uin or "").strip()
    if not uid:
        uid = _cfg_str("admin_qq") if payload.target == "friend" else _cfg_str("group")
    if not uid:
        raise HTTPException(400, "配置里没填 admin_qq / group，没法发测试消息")
    api = "send_private_msg" if payload.target == "friend" else "send_group_msg"
    key = "user_id" if payload.target == "friend" else "group_id"

    segments = []
    if payload.kind == "text":
        segments.append({"type": "text", "data": {"text": payload.text[:500]}})
    elif payload.kind == "music":
        segments.append({"type": "music", "data": {"type": "163", "id": str(payload.song_id)}})
    elif payload.kind == "sticker":
        folder = STICKERS / payload.mood
        files = [p for p in folder.iterdir() if p.suffix.lower() in {".png", ".jpg", ".gif", ".webp"}] if folder.exists() else []
        if not files:
            return JSONResponse({"ok": False, "error": f"没有 {payload.mood} 表情"})
        import random
        f = random.choice(files)
        b64 = base64.b64encode(f.read_bytes()).decode()
        segments.append({"type": "image", "data": {"file": "base64://" + b64}})
    else:
        raise HTTPException(400, "unknown kind")

    r = ob_call(api, {key: uid, "message": segments})
    return JSONResponse({"ok": r.get("status") == "ok", "resp": r})


# ---------------- 会话统计（给好友页用） ----------------
@app.get("/api/sessions/stats")
def session_stats() -> JSONResponse:
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
        rows = con.execute(
            "select conversation_id, user_id, content, updated_at from conversations"
        ).fetchall()
        con.close()
    except Exception:  # noqa: BLE001
        return JSONResponse({"items": {}})
    out = {}
    usage = _token_breakdown()
    for cid, umo, content, updated in rows:
        text = re.sub(r"data:image/[a-zA-Z]+;base64,[A-Za-z0-9+/=]+", "[图片]", content or "")
        text = re.sub(r"base64://[A-Za-z0-9+/=]+", "[图片]", text)
        out[str(umo)] = {
            "id": cid,
            "chars": len(text),
            "tokens": _scope_tokens(str(umo), usage),
            "updated": updated,
        }
    return JSONResponse({"items": out})


# ---------------- 会话 ----------------
@app.get("/api/sessions")
def sessions() -> JSONResponse:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    rows = con.execute(
        "select conversation_id, user_id, title, length(content), updated_at "
        "from conversations order by updated_at desc limit 30"
    ).fetchall()
    con.close()

    # 把 umo 解析成名字：只用昵称/群名，不显示号码
    groups: dict[str, str] = {}
    friends: dict[str, str] = {}
    try:
        for g in (ob_call("get_group_list").get("data") or []):
            groups[str(g.get("group_id"))] = g.get("group_name") or ""
    except Exception:  # noqa: BLE001
        pass
    try:
        for f in (ob_call("get_friend_list").get("data") or []):
            friends[str(f.get("user_id"))] = f.get("nickname") or f.get("remark") or ""
    except Exception:  # noqa: BLE001
        pass

    out = []
    usage = _token_breakdown()
    for cid, umo, title, chars, updated in rows:
        _, _, rest = (umo or "").partition(":")
        kind, _, target = rest.partition(":")
        if kind == "GroupMessage":
            label = "群聊"
            display = groups.get(target) or "一个群聊"
        elif kind == "FriendMessage":
            label = "私聊"
            display = friends.get(target) or "一个私聊"
        else:
            label = "其他"
            display = umo or "(未知)"
        out.append({"id": cid, "umo": umo, "title": title, "chars": chars,
                    "tokens": _scope_tokens(str(umo), usage), "updated": updated,
                    "kind": label, "target": target, "display": display})
    return JSONResponse(out)


class ClearSession(BaseModel):
    conversation_id: str


@app.post("/api/session/clear")
def clear_session(payload: ClearSession) -> JSONResponse:
    try:
        con = sqlite3.connect(DB, timeout=5)
        try:
            con.execute("update conversations set content = '[]', token_usage = 0 where conversation_id = ?",
                        (payload.conversation_id,))
            con.commit()
        finally:
            con.close()
    except sqlite3.OperationalError as exc:
        raise HTTPException(503, f"数据库忙，等一秒再点：{exc}") from exc
    return JSONResponse({"ok": True})


@app.post("/api/session/clear_all")
def clear_all_sessions() -> JSONResponse:
    """清空全部会话上下文，返回受影响行数。"""
    con = sqlite3.connect(DB, timeout=5)
    try:
        cur = con.execute("update conversations set content = '[]', token_usage = 0")
        n = cur.rowcount
        con.commit()
    finally:
        con.close()
    return JSONResponse({"ok": True, "affected": n})


# ---------------- 插件 ----------------
class PluginToggle(BaseModel):
    name: str


@app.post("/api/plugin/toggle")
def plugin_toggle(payload: PluginToggle) -> JSONResponse:
    name = payload.name
    src = PLUGINS / name
    dst = DISABLED / name
    try:
        if src.exists():
            DISABLED.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            state = "已停用"
        elif dst.exists():
            shutil.move(str(dst), str(src))
            state = "已启用"
        else:
            raise HTTPException(404, "找不到插件")
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)})
    return JSONResponse({"ok": True, "state": state, "note": "重启 AstrBot 后生效"})


@app.post("/api/plugin/delete")
def plugin_delete(payload: PluginToggle) -> JSONResponse:
    """彻底删掉插件目录（启用区或停用区），删之前自动打包留一份。"""
    name = payload.name or ""
    if not re.fullmatch(r"[\w.\-]+", name):
        raise HTTPException(400, "插件名不合法")
    target = None
    for base in (PLUGINS, DISABLED):
        p = base / name
        if p.exists():
            target = p
            break
    if target is None:
        raise HTTPException(404, "找不到插件")
    keep = LOG_DIR / "deleted_plugins"
    try:
        keep.mkdir(parents=True, exist_ok=True)
        shutil.make_archive(str(keep / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}"), "zip", str(target))
    except Exception:  # noqa: BLE001
        pass
    shutil.rmtree(target, ignore_errors=True)
    return JSONResponse({"ok": True, "note": f"{name} 已删除（原件也打包在 logs\\deleted_plugins），重启 AstrBot 生效"})


class PluginInstall(BaseModel):
    url: str


def _to_zip_url(url: str) -> list[str]:
    u = (url or "").strip().rstrip("/")
    if not u:
        return []
    if u.endswith(".zip"):
        return [u]
    m = re.search(r"github\.com/([^/]+)/([^/]+)", u)
    if m:
        owner, repo = m.group(1), m.group(2)
    elif re.fullmatch(r"[\w.\-]+/[\w.\-]+", u):
        owner, repo = u.split("/")
    else:
        return [u]
    return [
        f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/main",
        f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/master",
    ]


@app.post("/api/plugin/install")
def plugin_install(payload: PluginInstall) -> JSONResponse:
    """从 GitHub 仓库地址 / owner/repo / zip 链接装一个插件，并尝试装依赖。"""
    import io
    import tempfile
    import zipfile

    urls = _to_zip_url(payload.url)
    if not urls:
        raise HTTPException(400, "地址为空")
    blob = None
    for u in urls:
        try:
            r = requests.get(u, timeout=120)
            if r.status_code == 200 and len(r.content) > 2000:
                blob = r.content
                break
        except Exception:  # noqa: BLE001
            continue
    if not blob:
        raise HTTPException(502, "下载失败，检查地址或网络")

    with tempfile.TemporaryDirectory() as td:
        zipfile.ZipFile(io.BytesIO(blob)).extractall(td)
        meta = next(pathlib.Path(td).rglob("metadata.yaml"), None)
        if meta is None:
            raise HTTPException(400, "压缩包里没有 metadata.yaml，可能不是 AstrBot 插件")
        root = meta.parent
        name = re.sub(r"-(main|master)$", "", root.name)
        dst = PLUGINS / name
        if dst.exists() or (DISABLED / name).exists():
            raise HTTPException(409, f"插件 {name} 已经存在，先删掉再装")
        shutil.copytree(root, dst)

    note = "装好了，重启 AstrBot 生效"
    req = dst / "requirements.txt"
    if req.exists():
        try:
            r = subprocess.run(
                [PY, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", str(req)],
                capture_output=True, timeout=600,
            )
            note = "装好了，依赖已安装，重启 AstrBot 生效" if r.returncode == 0 else \
                "装好了，但依赖安装失败，请看插件日志"
        except Exception as exc:  # noqa: BLE001
            note = f"装好了，依赖安装超时或失败：{exc}"
    return JSONResponse({"ok": True, "name": name, "note": note})


# ---------------- 群友 ----------------
SERVICE_FRIEND_IDS = {"66600000", "2113929217", "2113929218", "2113929219"}
SERVICE_FRIEND_NAMES = {"小Q", "我的电脑", "我的手机", "我的Pad", "我的iPad", "我的Mac"}


def _is_real_friend(f: dict) -> bool:
    """过滤 QQ 自带的小Q、我的电脑/手机/Pad 这类服务账号。"""
    uid = str(f.get("user_id") or "")
    name = str(f.get("nickname") or f.get("remark") or "")
    if uid in SERVICE_FRIEND_IDS or name in SERVICE_FRIEND_NAMES:
        return False
    return uid != _bot_qq()


@app.get("/api/contacts")
def contacts() -> JSONResponse:
    groups = ob_call("get_group_list").get("data") or []
    friends = [f for f in (ob_call("get_friend_list").get("data") or []) if _is_real_friend(f)]
    return JSONResponse({
        "groups": [
            {"id": g.get("group_id"), "name": g.get("group_name"), "members": g.get("member_count")}
            for g in groups
        ],
        "friends": [
            {"id": f.get("user_id"), "name": f.get("nickname")}
            for f in friends
        ],
    })


# ---------------- 人物档案 ----------------
PROFILES = ROOT / "data" / "dafeiyu" / "profiles.json"
TOKEN_DB = ROOT / "data" / "dafeiyu" / "tokens.db"
SUMMARY_REQUESTS = ROOT / "data" / "dafeiyu" / "summary_requests.json"
PROFILE_RELATIONS = ("生人", "熟人", "主人")


def _token_summary() -> dict:
    """唯一 token 账本的汇总：全部历史、今日、最近五分钟、最近一分钟。"""
    now = time.time()
    midnight = datetime.datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()
    try:
        con = sqlite3.connect(f"file:{TOKEN_DB}?mode=ro", uri=True, timeout=5)
        row = con.execute(
            """select
                coalesce(sum(tokens), 0),
                coalesce(sum(case when ts >= ? then tokens else 0 end), 0),
                coalesce(sum(case when ts >= ? then tokens else 0 end), 0),
                coalesce(sum(case when ts >= ? then tokens else 0 end), 0)
                from usage""",
            (midnight, now - 300, now - 60),
        ).fetchone()
        con.close()
        all_time, today, five, one = (int(x or 0) for x in row)
    except Exception:  # noqa: BLE001
        all_time = today = five = one = 0
    return {
        "all_time": all_time,
        "today": today,
        "last_5m": five,
        "per_minute": one,
    }


def _token_breakdown() -> dict:
    """同一条记录按私聊、群、群成员、个人四种视角拆分。"""
    private: dict[str, int] = {}
    groups: dict[str, int] = {}
    members: dict[str, dict[str, int]] = {}
    people: dict[str, int] = {}
    try:
        con = sqlite3.connect(f"file:{TOKEN_DB}?mode=ro", uri=True, timeout=5)
        rows = con.execute(
            "select gid, uid, coalesce(sum(tokens), 0) from usage group by gid, uid"
        ).fetchall()
        con.close()
    except Exception:  # noqa: BLE001
        rows = []
    for gid, uid, tokens in rows:
        gid = str(gid or "")
        uid = str(uid or "")
        tokens = int(tokens or 0)
        if uid:
            people[uid] = people.get(uid, 0) + tokens
        if gid:
            groups[gid] = groups.get(gid, 0) + tokens
            if uid:
                bucket = members.setdefault(gid, {})
                bucket[uid] = bucket.get(uid, 0) + tokens
        elif uid:
            private[uid] = private.get(uid, 0) + tokens
    return {
        "private": private,
        "groups": groups,
        "members": members,
        "people": people,
    }


def _scope_tokens(umo: str, usage: dict | None = None) -> int:
    usage = usage or _token_breakdown()
    parts = str(umo or "").split(":", 2)
    if len(parts) < 3:
        return 0
    kind, target = parts[1], parts[2]
    if kind == "GroupMessage":
        return int(usage["groups"].get(target, 0))
    if kind == "FriendMessage":
        return int(usage["private"].get(target, 0))
    return 0


def _load_profiles() -> dict:
    try:
        data = json.loads(PROFILES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    users = data.get("users") if isinstance(data, dict) else {}
    return users if isinstance(users, dict) else {}


def _save_profiles(users: dict) -> None:
    PROFILES.parent.mkdir(parents=True, exist_ok=True)
    data = {"users": users}
    tmp = PROFILES.with_name(f"profiles.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, PROFILES)


@contextlib.contextmanager
def _profiles_lock():
    """和插件的档案模块共用一把文件锁，避免双进程互相覆盖。"""
    try:
        import msvcrt
    except ImportError:
        yield
        return
    lock = PROFILES.with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock, "a+b")
    try:
        f.seek(0, os.SEEK_END)
        if f.tell() == 0:
            f.write(b"0")
            f.flush()
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        f.close()


def _profile_row(qq: str, user: dict, people_tokens: dict[str, int]) -> dict:
    chars = int(user.get("chars") or 0)
    last = int(user.get("last_seen") or 0)
    total = int(people_tokens.get(qq) or 0)
    return {
        "qq": qq,
        "name": user.get("name") or qq,
        "alias": user.get("alias") or "",
        "relation": user.get("relation") or "生人",
        "likes": user.get("likes") or "",
        "notes": user.get("notes") or "",
        "msg_count": int(user.get("msg_count") or 0),
        "chars": chars,
        "groups": [str(g) for g in (user.get("groups") or [])],
        "tokens": total,
        "score": total,
        "last_seen": datetime.datetime.fromtimestamp(last).strftime("%m-%d %H:%M") if last else "",
    }


@app.get("/api/profiles")
def api_profiles() -> JSONResponse:
    users = _load_profiles()
    usage = _token_breakdown()
    items = [
        _profile_row(qq, u, usage["people"])
        for qq, u in users.items()
        if isinstance(u, dict)
    ]
    items.sort(key=lambda x: (x["score"], x["msg_count"]), reverse=True)
    return JSONResponse({"items": items})


@app.get("/api/token_rate")
def api_token_rate() -> JSONResponse:
    """唯一 token 账本的汇总，不再读 provider_stats 或 jsonl。"""
    return JSONResponse(_token_summary())


@app.get("/api/token_usage")
def api_token_usage() -> JSONResponse:
    """总览和好友页共用的一份账本拆分。"""
    data = _token_summary()
    data.update(_token_breakdown())
    return JSONResponse(data)


class ProfileIn(BaseModel):
    qq: str
    alias: str = ""
    relation: str = ""
    notes: str = ""


@app.post("/api/profile")
def api_profile_save(payload: ProfileIn) -> JSONResponse:
    with _profiles_lock():
        users = _load_profiles()
        user = users.get(payload.qq)
        if not isinstance(user, dict):
            raise HTTPException(404, "没有这个人的档案")
        user["alias"] = payload.alias.strip()[:40]
        if payload.relation in PROFILE_RELATIONS:
            user["relation"] = payload.relation
        user["notes"] = payload.notes.strip()[:800]
        _save_profiles(users)
    return JSONResponse({"ok": True})


class SummarizeIn(BaseModel):
    gid: str = ""
    qq: str = ""


@app.post("/api/profiles/summarize")
def api_profile_summarize(payload: SummarizeIn) -> JSONResponse:
    gid = payload.gid.strip()
    qq = payload.qq.strip()
    if not gid and not qq:
        raise HTTPException(400, "要给群号或 QQ")
    try:
        items = json.loads(SUMMARY_REQUESTS.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            items = []
    except (OSError, ValueError):
        items = []
    items.append({"gid": gid, "qq": qq, "ts": time.time()})
    SUMMARY_REQUESTS.parent.mkdir(parents=True, exist_ok=True)
    tmp = SUMMARY_REQUESTS.with_name(f"summary_requests.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(items[-20:], ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, SUMMARY_REQUESTS)
    return JSONResponse({"ok": True, "note": "已提交，十几秒后刷新档案"})


# ---------------- 配置 ----------------
@app.get("/api/config_raw")
def config_raw() -> JSONResponse:
    return JSONResponse(read_cfg())


class ConfigIn(BaseModel):
    data: dict


@app.post("/api/config_raw")
def config_save(payload: ConfigIn) -> JSONResponse:
    backup = CFG.with_name(f"cmd_config.backup.{int(time.time())}.json")
    shutil.copy2(CFG, backup)
    write_cfg(payload.data)
    return JSONResponse({"ok": True, "backup": backup.name,
                         "note": "已保存并备份，部分项需要重启 AstrBot"})


# ---------------- 其余（沿用 v1） ----------------
@app.get("/api/image/{name}")
def image_file(name: str):
    path = (IMG_DIR / name).resolve()
    if str(path).startswith(str(IMG_DIR.resolve())) and path.exists():
        return FileResponse(path)
    # 兜底：QQ 图片缓存
    for p, _mt in _qq_cache_files():
        if p.name == name:
            return FileResponse(p)
    raise HTTPException(404)


@app.get("/api/logs")
def logs(name: str = "astrbot", lines: int = 150) -> JSONResponse:
    mapping = {
        "astrbot": LOG_DIR / "astrbot.out.log", "astrbot_err": LOG_DIR / "astrbot.err.log",
        "snowluma": LOG_DIR / "snowluma.out.log", "watchdog": LOG_DIR / "watchdog.log",
        "panel": LOG_DIR / "panel.err.log",
    }
    path = mapping.get(name)
    if not path:
        raise HTTPException(400, "unknown log")
    return JSONResponse({"name": name, "content": tail(path, lines)})


class Action(BaseModel):
    name: str


@app.post("/api/action")
def action(payload: Action) -> JSONResponse:
    if payload.name == "restart_astrbot":
        restart_astrbot(); return JSONResponse({"ok": True, "msg": "AstrBot 重启中"})
    if payload.name == "restart_snowluma":
        restart_snowluma(); return JSONResponse({"ok": True, "msg": "SnowLuma 重启中"})
    if payload.name == "backup":
        started = _start_backup()
        return JSONResponse({
            "ok": True,
            "started": started,
            "msg": "备份已开始" if started else "正在备份，请稍等",
        })
    if payload.name == "audit":
        ok, text = _doctor_text()
        return JSONResponse({"ok": ok, "msg": "体检完成", "text": text})
    if payload.name == "start_all":
        run_bg(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(ROOT / "start.ps1")])
        return JSONResponse({"ok": True, "msg": "启动全部"})
    raise HTTPException(400, "unknown action")


@app.post("/api/backup")
def backup_now() -> JSONResponse:
    started = _start_backup()
    return JSONResponse({
        "ok": True,
        "started": started,
        "msg": "备份已开始" if started else "正在备份，请稍等",
    })


@app.get("/api/backup/status")
def backup_status() -> JSONResponse:
    return JSONResponse(_backup_status())


@app.get("/api/backup/config")
def backup_config_get() -> JSONResponse:
    return JSONResponse(_backup_config_read())


@app.post("/api/backup/config")
def backup_config_save(payload: dict) -> JSONResponse:
    return JSONResponse(_backup_config_write(payload))


class Toggle(BaseModel):
    key: str
    value: bool


def _apply_toggle(key: str, value: bool, cfg: dict) -> bool:
    ps = cfg.setdefault("platform_settings", {})
    ltm = cfg.setdefault("provider_ltm_settings", {})
    if key == "reply_all":
        cfg["wake_prefix"] = [""] if value else ["/"]
    elif key == "segmented":
        ps.setdefault("segmented_reply", {})["enable"] = value
    elif key == "group_icl":
        ltm["group_icl_enable"] = value
    elif key == "web_search":
        cfg.setdefault("provider_settings", {})["web_search"] = value
    else:
        return False
    return True


def _toggle_val(key: str, cfg: dict):
    ps = cfg.get("platform_settings") or {}
    ltm = cfg.get("provider_ltm_settings") or {}
    if key == "reply_all":
        return cfg.get("wake_prefix") == [""]
    if key == "segmented":
        return bool((ps.get("segmented_reply") or {}).get("enable"))
    if key == "group_icl":
        return bool(ltm.get("group_icl_enable"))
    if key == "web_search":
        return bool((cfg.get("provider_settings") or {}).get("web_search"))
    return None


VOICE_CFG = DATA / "config" / "astrbot_plugin_dafeiyu_voice_config.json"
QZ_AI_STATE = ROOT / "data" / "qzone_ai_state.json"


def _voice_cfg() -> dict:
    try:
        return json.loads(VOICE_CFG.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


@app.get("/api/overview_toggles")
def api_overview_toggles() -> JSONResponse:
    cfg = read_cfg()
    ai = {}
    try:
        ai = json.loads(QZ_AI_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return JSONResponse({
        "voice": bool(_effective_voice_cfg().get("voice_enable", True)),
        "web_search": bool(_toggle_val("web_search", cfg)),
        "reply_all": bool(_toggle_val("reply_all", cfg)),
        "group_icl": bool(_toggle_val("group_icl", cfg)),
        "qzone_ai": bool(ai.get("enabled", True)),
    })


class OverviewToggleIn(BaseModel):
    key: str
    value: bool


@app.post("/api/overview_toggles")
def api_overview_toggle_set(payload: OverviewToggleIn) -> JSONResponse:
    if payload.key == "voice":
        cfg = _voice_cfg()
        cfg["voice_enable"] = payload.value
        VOICE_CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=4), encoding="utf-8")
        return JSONResponse({"ok": True})
    if payload.key == "qzone_ai":
        try:
            st = json.loads(QZ_AI_STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            st = {}
        st["enabled"] = payload.value
        QZ_AI_STATE.parent.mkdir(parents=True, exist_ok=True)
        QZ_AI_STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        return JSONResponse({"ok": True})
    cfg = read_cfg()
    if not _apply_toggle(payload.key, payload.value, cfg):
        raise HTTPException(400, "未知开关")
    write_cfg(cfg)
    # 语音和 AI 说说由插件每次读文件，立即生效；其余是 AstrBot 核心配置，要重启
    return JSONResponse({"ok": True, "restart": payload.key not in ("voice", "qzone_ai")})


@app.post("/api/toggle")
def toggle(payload: Toggle) -> JSONResponse:
    cfg = read_cfg()
    if not _apply_toggle(payload.key, payload.value, cfg):
        raise HTTPException(400, "unknown key")
    write_cfg(cfg)
    return JSONResponse({"ok": True, "key": payload.key, "value": payload.value,
                         "note": "部分开关需要重启 AstrBot"})


# ---------------- 快捷总控：最常改的参数集中一处 ----------------
QUICK_SPEC = [
    ("对话", "@toggle", "reply_all", "bool", "全部消息都回", "关掉就只接 / 开头的"),
    ("对话", "@toggle", "segmented", "bool", "分段回复", "长回复拆成几条发，更像真人"),
    ("对话", "@toggle", "group_icl", "bool", "群聊上下文", "让它看到群里前面在聊什么"),
    ("对话", "@toggle", "web_search", "bool", "联网搜索", "拿不准的事先查再答"),
    ("语音", "astrbot_plugin_dafeiyu_voice", "voice_enable", "bool", "语音开关", "开着它才可能用声音回，发不发由模型判断"),
    ("语音", "astrbot_plugin_dafeiyu_voice", "max_chars", "int", "语音字数上限", "建议 60~150，越长合成越慢"),
    ("主动消息", "astrbot_plugin_proactive_chat", "group_settings.enable", "bool", "群里主动说话", "冷场时自己开话题"),
    ("主动消息", "astrbot_plugin_proactive_chat", "group_settings.session_list", "list", "在哪些会话里主动说话", "可以多选；不选就是哪都不说"),
    ("主动消息", "astrbot_plugin_proactive_chat", "group_settings.schedule_settings.min_interval_minutes", "int", "最短间隔（分钟）", ""),
]


def _quick_path(plugin: str) -> pathlib.Path:
    if plugin == "@main":
        return CFG
    return DATA / "config" / f"{plugin}_config.json"


def _quick_get(data: dict, path: str):
    cur = data
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _quick_set(data: dict, path: str, value) -> None:
    keys = path.split(".")
    cur = data
    for k in keys[:-1]:
        nxt = cur.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[k] = nxt
        cur = nxt
    cur[keys[-1]] = value


@app.get("/api/quick")
def quick_get() -> JSONResponse:
    # 群/好友的显示名，给下拉菜单用，避免让用户看一串 umo
    options: list[dict] = []
    try:
        for g in (ob_call("get_group_list").get("data") or []):
            options.append({
                "value": f"snowluma:GroupMessage:{g.get('group_id')}",
                "label": f"群 · {g.get('group_name') or g.get('group_id')}",
            })
        for f in (ob_call("get_friend_list").get("data") or []):
            options.append({
                "value": f"snowluma:FriendMessage:{f.get('user_id')}",
                "label": f"好友 · {f.get('nickname') or f.get('remark') or f.get('user_id')}",
            })
    except Exception:  # noqa: BLE001
        pass

    out = []
    for group, plugin, path, typ, label, hint in QUICK_SPEC:
        val = None
        if plugin == "@toggle":
            val = _toggle_val(path, read_cfg())
        else:
            try:
                val = _quick_get(json.loads(_quick_path(plugin).read_text(encoding="utf-8-sig")), path)
            except Exception:  # noqa: BLE001
                pass
        item = {"group": group, "plugin": plugin, "path": path, "type": typ,
                "label": label, "hint": hint, "value": val}
        if path.endswith("session_list"):
            item["options"] = options
        out.append(item)
    return JSONResponse({"items": out})


class QuickSet(BaseModel):
    plugin: str
    path: str
    value: object


@app.post("/api/quick")
def quick_set(payload: QuickSet) -> JSONResponse:
    # 行为开关走单独的写逻辑
    if payload.plugin == "@toggle":
        cfg = read_cfg()
        if not _apply_toggle(payload.path, bool(payload.value), cfg):
            raise HTTPException(400, f"未知开关：{payload.path}")
        write_cfg(cfg)
        return JSONResponse({"ok": True, "note": "已保存"})

    p = _quick_path(payload.plugin)
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else {}
    except Exception:  # noqa: BLE001
        data = {}
    _quick_set(data, payload.path, payload.value)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")
    return JSONResponse({"ok": True, "note": "已保存"})


# ---------------- 模型工具（和 AstrBot 面板联动） ----------------
def _astrbot_headers() -> dict:
    cfg = read_cfg()
    dash = cfg.get("dashboard") or {}
    secret = dash.get("jwt_secret") or ""
    if not secret:
        raise HTTPException(500, "cmd_config 里没有 dashboard.jwt_secret")
    token = jwt.encode(
        {
            "username": dash.get("username") or "astrbot",
            "exp": datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=1),
        },
        secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@app.get("/api/aitools")
def api_aitools() -> JSONResponse:
    """读 AstrBot 的工具清单，控制台里看哪些开着、哪些关了。"""
    try:
        r = requests.get(
            "http://127.0.0.1:6185/api/v1/tools",
            headers=_astrbot_headers(),
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"items": [], "error": f"连不上 AstrBot：{exc}"[:160]})
    if r.status_code != 200:
        return JSONResponse({"items": [], "error": f"AstrBot 返回 {r.status_code}"})
    items = []
    for t in r.json().get("data") or []:
        origin = t.get("origin_name") or t.get("origin") or ""
        items.append(
            {
                "name": t.get("name") or "",
                "desc": (t.get("description") or "")[:100],
                "origin": origin,
                "active": bool(t.get("active")),
                "builtin": origin == "AstrBot Core",
            }
        )
    return JSONResponse({"items": items})


class ToolToggleIn(BaseModel):
    name: str
    enabled: bool


@app.post("/api/aitools/toggle")
def api_aitools_toggle(payload: ToolToggleIn) -> JSONResponse:
    """开关插件工具。AstrBot 自带的只读，会原样返回它的报错。"""
    from urllib.parse import quote

    url = (
        "http://127.0.0.1:6185/api/v1/tools/"
        + quote(payload.name, safe="")
        + "/enabled"
    )
    try:
        r = requests.patch(
            url,
            headers=_astrbot_headers(),
            json={"enabled": payload.enabled},
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "msg": f"连不上 AstrBot：{exc}"[:160]})
    try:
        msg = r.json().get("message") or ""
    except ValueError:
        msg = r.text[:120]
    return JSONResponse({"ok": r.status_code == 200, "msg": msg})


@app.get("/api/health")
def health() -> JSONResponse:
    """一眼看健康：模型、视觉、语音、最近报错。"""
    cfg = read_cfg()
    provs = {p.get("id"): p for p in cfg.get("provider", [])}
    chat = provs.get("deepseek", {})
    vision = provs.get("glm4v", {})

    errs: list[str] = []
    log = LOG_DIR / "astrbot.out.log"
    if log.exists():
        try:
            text = tail(log, 300)
        except Exception:  # noqa: BLE001
            text = ""
        for line in text.splitlines():
            s = re.sub(r"\x1b\[[0-9;]*m", "", line)
            if "[ERRO]" in s:
                errs.append(s.strip()[:200])

    voice_cfg = _voice_cfg()

    # QQ 是否还登着：能拿到昵称才算在线，端口在不算
    qq_online, qq_name = False, ""
    try:
        info = login_cached()
        if info.get("status") == "ok":
            qq_online = True
            qq_name = ((info.get("data") or {}).get("nickname") or "")
    except Exception:  # noqa: BLE001
        pass

    return JSONResponse({
        "chat_model": chat.get("model", ""),
        "vision_model": vision.get("model", ""),
        "tts": port_open(9880),
        "voice_on": bool(voice_cfg.get("voice_enable")),
        "qq_online": qq_online,
        "qq_name": qq_name,
        "error_count": len(errs),
        "errors": errs[-6:],
        "plugins_on": len([d for d in PLUGINS.iterdir() if d.is_dir()]) if PLUGINS.exists() else 0,
    })


PERSONA_HISTORY = ROOT / "data" / "persona_history.jsonl"


def _persona_snapshot(text: str) -> None:
    if not text.strip():
        return
    lines: list[str] = []
    try:
        lines = PERSONA_HISTORY.read_text(encoding="utf-8").splitlines()
    except OSError:
        pass
    entry = json.dumps({"ts": time.time(), "text": text}, ensure_ascii=False)
    lines.append(entry)
    PERSONA_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    PERSONA_HISTORY.write_text("\n".join(lines[-40:]) + "\n", encoding="utf-8")


@app.get("/api/persona")
def persona() -> JSONResponse:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    row = con.execute(
        "select persona_id, system_prompt from personas where persona_id != 'default' "
        "order by updated_at desc limit 1"
    ).fetchone()
    con.close()
    if not row:
        raise HTTPException(404, "没有人格")
    return JSONResponse({"id": row[0], "prompt": row[1]})


class PersonaIn(BaseModel):
    prompt: str


@app.post("/api/persona")
def save_persona(payload: PersonaIn) -> JSONResponse:
    con = sqlite3.connect(DB, timeout=20)
    old = con.execute(
        "select system_prompt from personas where persona_id != 'default' order by updated_at desc limit 1"
    ).fetchone()
    if old and old[0] and old[0] != payload.prompt:
        _persona_snapshot(old[0])
    con.execute(
        "update personas set system_prompt = ?, updated_at = datetime('now') "
        "where persona_id != 'default'", (payload.prompt,))
    con.commit(); con.close()
    (ROOT / "personas" / "大肥鱼.txt").write_text(payload.prompt, encoding="utf-8", newline="\n")
    return JSONResponse({"ok": True, "msg": "已保存，重启 AstrBot 后完全生效"})


@app.get("/api/persona/history")
def api_persona_history() -> JSONResponse:
    items = []
    try:
        for i, line in enumerate(PERSONA_HISTORY.read_text(encoding="utf-8").splitlines()):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            text = rec.get("text") or ""
            items.append({"idx": i, "ts": rec.get("ts"), "chars": len(text),
                          "preview": text.strip().splitlines()[0][:40] if text.strip() else ""})
    except OSError:
        pass
    return JSONResponse({"items": list(reversed(items))})


class PersonaRestoreIn(BaseModel):
    idx: int


@app.post("/api/persona/restore")
def api_persona_restore(payload: PersonaRestoreIn) -> JSONResponse:
    try:
        lines = PERSONA_HISTORY.read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[payload.idx])
    except (OSError, ValueError, IndexError):
        raise HTTPException(404, "找不到这个版本")
    text = rec.get("text") or ""
    if not text.strip():
        raise HTTPException(400, "这个版本是空的")
    con = sqlite3.connect(DB, timeout=20)
    con.execute(
        "update personas set system_prompt = ?, updated_at = datetime('now') where persona_id != 'default'",
        (text,),
    )
    con.commit()
    con.close()
    (ROOT / "personas" / "大肥鱼.txt").write_text(text, encoding="utf-8", newline="\n")
    _persona_snapshot(text)
    return JSONResponse({"ok": True, "msg": "已恢复到这一版，重启 AstrBot 后完全生效"})


# ---------------- 唱歌（RVC） ----------------
@app.get("/api/rvc")
def api_rvc() -> JSONResponse:
    """扫描 rvc\\models 下的唱歌模型，顺便看看运行时装好没有。"""
    models = []
    if RVC_MODELS.exists():
        for d in sorted(RVC_MODELS.iterdir()):
            if not d.is_dir():
                continue
            pths = sorted(d.glob("*.pth"))
            if not pths:
                continue
            idx = sorted(d.glob("*.index"))
            size = sum(f.stat().st_size for f in pths + idx)
            models.append({
                "name": d.name,
                "pth": pths[0].name,
                "index": idx[0].name if idx else "",
                "mb": round(size / 1048576, 1),
            })
    try:
        current = json.loads(RVC_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}

    runtime = ""
    candidates = [RVC_ROOT / "runtime" / "python.exe"]
    candidates += list(RVC_ROOT.glob("*/runtime/python.exe"))
    candidates += list(RVC_ROOT.glob("pkg/*/runtime/python.exe"))
    for cand in candidates:
        if cand.exists():
            runtime = str(cand)
            break
    try:
        lang_map = json.loads((RVC_ROOT / "model_map.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        lang_map = {}
    return JSONResponse({
        "models": models,
        "current": current,
        "runtime": runtime,
        "runtime_ready": bool(runtime),
        "root": str(RVC_ROOT),
        "map": lang_map,
    })


class RvcIn(BaseModel):
    name: str


class SingTestIn(BaseModel):
    song: str
    seconds: int = 25


@app.post("/api/sing_test")
def api_sing_test(payload: SingTestIn) -> JSONResponse:
    """在控制台试唱一首：跑 singing 流水线，生成 mp3 后返回播放地址。"""
    song = (payload.song or "").strip()
    if not song:
        raise HTTPException(400, "歌名不能为空")
    SING_DIR.mkdir(parents=True, exist_ok=True)
    out = SING_DIR / f"test_{int(time.time())}.mp3"
    seconds = max(5, min(payload.seconds, 45))
    cmd = [
        PY, "-X", "utf8", str(ROOT / "scripts" / "sing_song.py"),
        "--song", song, "--out", str(out), "--max-seconds", str(seconds),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", timeout=300)
    except subprocess.TimeoutExpired:
        return JSONResponse({"ok": False, "error": "超过 5 分钟还没唱完"})
    if r.returncode != 0 or not out.exists():
        tail = (r.stdout or "") + (r.stderr or "")
        return JSONResponse({"ok": False, "error": tail[-600:] or "唱歌失败"})
    return JSONResponse({"ok": True, "url": f"/api/sing_audio/{out.name}", "seconds": seconds})


@app.get("/api/sing_audio/{name}")
def api_sing_audio(name: str):
    path = SING_DIR / name
    if not path.exists() or path.suffix.lower() != ".mp3":
        raise HTTPException(404, "没有这条试唱")
    return FileResponse(path, media_type="audio/mpeg")


@app.get("/api/persona_check")
def api_persona_check() -> JSONResponse:
    """跑一次人格体检，返回 ERROR/WARN 统计。"""
    try:
        r = subprocess.run(
            [PY, "-X", "utf8", str(ROOT / "scripts" / "check_persona.py"), "--json", "--last", "80"],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=120,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "体检超时"})
    try:
        return JSONResponse(json.loads(r.stdout))
    except (TypeError, ValueError):
        return JSONResponse({"error": ((r.stdout or "") + (r.stderr or ""))[-400:]})


@app.post("/api/rvc/switch")
def api_rvc_switch(payload: RvcIn) -> JSONResponse:
    target = RVC_MODELS / payload.name
    if not target.is_dir():
        raise HTTPException(404, "没有这个模型")
    pths = sorted(target.glob("*.pth"))
    if not pths:
        raise HTTPException(400, "这个文件夹里没有 .pth")
    idx = sorted(target.glob("*.index"))
    rec = {
        "name": payload.name,
        "pth": str(pths[0]),
        "index": str(idx[0]) if idx else "",
    }
    RVC_STATE.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return JSONResponse({"ok": True, "current": rec})


# ---------------- AI 说说 ----------------
AI_STATE = ROOT / "data" / "qzone_ai_state.json"
AI_DRAFTS = ROOT / "data" / "qzone_drafts.json"


def _ai_load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _ai_save(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


@app.get("/api/qzone_ai")
def api_qzone_ai() -> JSONResponse:
    st = _ai_load(AI_STATE, {"enabled": True, "mode": "draft", "interval_min": 90, "last_post_date": ""})
    drafts = _ai_load(AI_DRAFTS, [])
    return JSONResponse({"state": st, "drafts": drafts})


class AiSetIn(BaseModel):
    enabled: bool | None = None
    mode: str | None = None


@app.post("/api/qzone_ai/set")
def api_qzone_ai_set(payload: AiSetIn) -> JSONResponse:
    st = _ai_load(AI_STATE, {"enabled": True, "mode": "draft", "interval_min": 90, "last_post_date": ""})
    if payload.enabled is not None:
        st["enabled"] = payload.enabled
    if payload.mode:
        if payload.mode not in ("draft", "auto"):
            raise HTTPException(400, "mode 只能是 draft 或 auto")
        st["mode"] = payload.mode
    _ai_save(AI_STATE, st)
    return JSONResponse({"ok": True, "state": st})


@app.post("/api/qzone_ai/check")
def api_qzone_ai_check() -> JSONResponse:
    st = _ai_load(AI_STATE, {"enabled": True, "mode": "draft", "interval_min": 90, "last_post_date": ""})
    st["force"] = True
    _ai_save(AI_STATE, st)
    return JSONResponse({"ok": True, "msg": "已经让它现在判断一次，稍等一两分钟"})


class AiDraftIn(BaseModel):
    id: str
    action: str


@app.get("/api/qzone_diary")
def api_qzone_diary() -> JSONResponse:
    st = _ai_load(AI_STATE, {})
    return JSONResponse({
        "enabled": bool(st.get("diary_enabled", True)),
        "time": st.get("diary_time") or "20:00",
        "last": st.get("last_diary_date") or "",
        "result": st.get("last_diary_result") or "",
        "pending": bool(st.get("force_diary")),
    })


class DiarySetIn(BaseModel):
    enabled: bool | None = None
    time: str | None = None
    now: bool = False


@app.post("/api/qzone_diary")
def api_qzone_diary_set(payload: DiarySetIn) -> JSONResponse:
    st = _ai_load(AI_STATE, {})
    if payload.enabled is not None:
        st["diary_enabled"] = payload.enabled
    if payload.time:
        if not re.match(r"^\d{2}:\d{2}$", payload.time):
            raise HTTPException(400, "时间要 HH:MM")
        st["diary_time"] = payload.time
    if payload.now:
        st["force_diary"] = True
    _ai_save(AI_STATE, st)
    return JSONResponse({"ok": True})


@app.post("/api/qzone_ai/draft")
def api_qzone_ai_draft(payload: AiDraftIn) -> JSONResponse:
    drafts = _ai_load(AI_DRAFTS, [])
    item = next((d for d in drafts if str(d.get("id")) == str(payload.id)), None)
    if not item:
        return JSONResponse({"ok": False, "error": "草稿不存在"})
    if payload.action == "discard":
        drafts = [d for d in drafts if str(d.get("id")) != str(payload.id)]
        _ai_save(AI_DRAFTS, drafts)
        return JSONResponse({"ok": True})
    if payload.action != "publish":
        raise HTTPException(400, "action 只能是 publish 或 discard")
    try:
        r = subprocess.run(
            [PY, "-X", "utf8", str(ROOT / "scripts" / "qzone_publish.py"), "--text", item["text"]],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)})
    ok = '"ok": true' in (r.stdout or "").lower()
    if ok:
        drafts = [d for d in drafts if str(d.get("id")) != str(payload.id)]
        _ai_save(AI_DRAFTS, drafts)
        st = _ai_load(AI_STATE, {})
        st["last_post_date"] = time.strftime("%Y-%m-%d")
        _ai_save(AI_STATE, st)
    return JSONResponse({"ok": ok, "out": (r.stdout or "")[:200], "err": (r.stderr or "")[:200]})


# ---------------- 黑白名单 ----------------
ACCESS_STORE = ROOT / "data" / "access_lists.json"


def _load_access() -> dict:
    try:
        return json.loads(ACCESS_STORE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"mode": "whitelist", "allow_all_groups": False, "auto_whitelist_new_friend": False,
                "whitelist": {"users": [], "groups": []},
                "blacklist": {"users": [], "groups": []}}


def _astrbot_whitelist_ids(data: dict) -> list[str]:
    """把控制台名单翻译成 AstrBot 的 session id。"""
    ids: list[str] = []
    users = [str(x) for x in (data.get("whitelist") or {}).get("users", [])]
    admin = _cfg_str("admin_qq")
    if admin and admin not in users:
        users.append(admin)
    for uid in users:
        ids.append(f"snowluma:FriendMessage:{uid}")
    groups = {str(x) for x in (data.get("whitelist") or {}).get("groups", [])}
    for gid in sorted(groups):
        ids.append(f"snowluma:GroupMessage:{gid}")
    return ids


def _sync_astrbot_whitelist() -> None:
    """把控制台的名单写进 AstrBot 自带白名单，两边保持一份。"""
    data = _load_access()
    enabled = data.get("mode") == "whitelist"
    ids = _astrbot_whitelist_ids(data) if enabled else []
    try:
        cfg = read_cfg()
    except Exception:  # noqa: BLE001
        return
    ps = cfg.setdefault("platform_settings", {})
    if ps.get("enable_id_white_list") == enabled and list(ps.get("id_whitelist") or []) == ids:
        return
    ps["enable_id_white_list"] = enabled
    ps["id_whitelist"] = ids
    try:
        write_cfg(cfg)
        print(f"[access] 同步 AstrBot 白名单：{len(ids)} 个会话")
    except Exception as exc:  # noqa: BLE001
        print(f"[access] 同步 AstrBot 白名单失败: {exc}")


def _save_access(data: dict) -> None:
    ACCESS_STORE.parent.mkdir(parents=True, exist_ok=True)
    ACCESS_STORE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        _sync_astrbot_whitelist()
    except Exception:  # noqa: BLE001
        pass


@app.get("/api/access")
def api_access() -> JSONResponse:
    return JSONResponse(_load_access())


def _access_status() -> dict:
    data = _load_access()
    mode = data.get("mode") or "whitelist"
    expected = _astrbot_whitelist_ids(data) if mode == "whitelist" else []
    try:
        cfg = read_cfg()
        ps = cfg.get("platform_settings") or {}
        actual = [str(x) for x in (ps.get("id_whitelist") or [])]
        enable = bool(ps.get("enable_id_white_list"))
    except Exception:  # noqa: BLE001
        actual, enable = [], False
    expected_set, actual_set = set(expected), set(actual)
    return {
        "mode": mode,
        "synced": enable == (mode == "whitelist") and expected_set == actual_set,
        "expected": len(expected),
        "actual": len(actual),
        "missing": sorted(expected_set - actual_set),
        "extra": sorted(actual_set - expected_set),
    }


@app.get("/api/access/status")
def api_access_status() -> JSONResponse:
    return JSONResponse(_access_status())


@app.post("/api/access/sync")
def api_access_sync() -> JSONResponse:
    try:
        _sync_astrbot_whitelist()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "msg": f"同步失败：{exc}"})
    return JSONResponse({"ok": True, "msg": "名单已同步", "status": _access_status()})


class AccessModeIn(BaseModel):
    mode: str


@app.post("/api/access/mode")
def api_access_mode(payload: AccessModeIn) -> JSONResponse:
    if payload.mode not in ("all", "whitelist"):
        raise HTTPException(400, "mode 只能是 all 或 whitelist")
    data = _load_access()
    data["mode"] = payload.mode
    _save_access(data)
    return JSONResponse({"ok": True, "mode": payload.mode})


class AccessToggleIn(BaseModel):
    side: str
    kind: str
    id: str
    on: bool


@app.post("/api/access/toggle")
def api_access_toggle(payload: AccessToggleIn) -> JSONResponse:
    if payload.side not in ("whitelist", "blacklist") or payload.kind not in ("users", "groups"):
        raise HTTPException(400, "side/kind 不对")
    data = _load_access()
    data[payload.side].setdefault(payload.kind, [])
    items = [str(x) for x in data[payload.side][payload.kind] if str(x) != str(payload.id)]
    if payload.on:
        items.append(str(payload.id))
        other = "blacklist" if payload.side == "whitelist" else "whitelist"
        data[other].setdefault(payload.kind, [])
        data[other][payload.kind] = [str(x) for x in data[other][payload.kind] if str(x) != str(payload.id)]
    data[payload.side][payload.kind] = items
    _save_access(data)
    return JSONResponse({"ok": True, "side": payload.side, "kind": payload.kind, "id": payload.id, "on": payload.on})


class AccessBatchIn(BaseModel):
    side: str
    kind: str = "users"
    ids: list[str]


@app.post("/api/access/batch")
def api_access_batch(payload: AccessBatchIn) -> JSONResponse:
    if payload.side not in ("whitelist", "blacklist") or payload.kind not in ("users", "groups"):
        raise HTTPException(400, "side/kind 不对")
    ids = [str(x).strip() for x in payload.ids if str(x).strip()]
    data = _load_access()
    other_side = "blacklist" if payload.side == "whitelist" else "whitelist"
    cur = {str(x) for x in data[payload.side].get(payload.kind, [])}
    cur.update(ids)
    data[payload.side][payload.kind] = sorted(cur)
    data[other_side][payload.kind] = [str(x) for x in data[other_side].get(payload.kind, []) if str(x) not in ids]
    _save_access(data)
    return JSONResponse({"ok": True, "added": len(ids), "side": payload.side, "kind": payload.kind})


# ---------------- 新好友监视 ----------------
NEW_FRIENDS = ROOT / "data" / "new_friends.json"
FRIEND_SNAPSHOT = ROOT / "data" / "friend_snapshot.json"


def _load_json_list(path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _friend_watcher() -> None:
    """每两分钟比对一次好友列表，补上漏掉的「有人加我」提醒。"""
    while True:
        try:
            _sync_astrbot_whitelist()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(120)
        try:
            data = [f for f in (ob_call("get_friend_list").get("data") or []) if _is_real_friend(f)]
            current = {str(f.get("user_id")): (f.get("nickname") or f.get("remark") or "") for f in data}
            if not current:
                continue
            try:
                snapshot = json.loads(FRIEND_SNAPSHOT.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                snapshot = None
            if snapshot is None:
                _save_json(FRIEND_SNAPSHOT, current)
                continue
            added = [uid for uid in current if uid not in snapshot]
            if added:
                items = _load_json_list(NEW_FRIENDS)
                requests = []
                known_flags = {r.get("flag") for r in requests}
                for uid in added:
                    items.append({"user_id": uid, "name": current.get(uid, ""), "time": time.strftime("%m-%d %H:%M")})
                    flag = f"newfriend:{uid}"
                    if flag not in known_flags:
                        requests.append({
                            "flag": flag,
                            "type": "new_friend",
                            "sub_type": "",
                            "user_id": uid,
                            "group_id": None,
                            "comment": "QQ 已通过好友，等你确认",
                            "time": time.strftime("%m-%d %H:%M"),
                        })
                _save_json(NEW_FRIENDS, items[-50:])
            _save_json(FRIEND_SNAPSHOT, current)
            _sync_astrbot_whitelist()
        except Exception:  # noqa: BLE001
            continue


threading.Thread(target=_friend_watcher, daemon=True).start()


@app.get("/api/new_friends")
def api_new_friends() -> JSONResponse:
    return JSONResponse({"items": _load_json_list(NEW_FRIENDS)})


@app.post("/api/new_friends/clear")
def api_new_friends_clear() -> JSONResponse:
    _save_json(NEW_FRIENDS, [])
    return JSONResponse({"ok": True})


# ---------------- QQ 空间 ----------------
QZ_CFG = DATA / "config" / "astrbot_plugin_qzone_config.json"
QZ_DEFAULT = {"publish": "30 23 * * *", "comment": "0 8 * * *"}


@app.get("/api/qzone")
def api_qzone() -> JSONResponse:
    try:
        cfg = json.loads(QZ_CFG.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return JSONResponse({"ok": False, "error": "没找到空间插件配置"})
    trig = cfg.get("trigger") or {}
    return JSONResponse({
        "ok": True,
        "publish": bool((trig.get("publish_cron") or "").strip()),
        "comment": bool((trig.get("comment_cron") or "").strip()),
        "publish_cron": trig.get("publish_cron", ""),
        "comment_cron": trig.get("comment_cron", ""),
    })


class QzSwitchIn(BaseModel):
    key: str
    on: bool


@app.post("/api/qzone/switch")
def api_qzone_switch(payload: QzSwitchIn) -> JSONResponse:
    if payload.key not in QZ_DEFAULT:
        raise HTTPException(400, "key 只能是 publish 或 comment")
    try:
        cfg = json.loads(QZ_CFG.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return JSONResponse({"ok": False, "error": "没找到空间插件配置"})
    trig = cfg.setdefault("trigger", {})
    trig[f"{payload.key}_cron"] = QZ_DEFAULT[payload.key] if payload.on else ""
    QZ_CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return JSONResponse({"ok": True, "need_restart": True})


# ---------------- 屏幕 ----------------
@app.post("/api/screen/off")
def api_screen_off() -> JSONResponse:
    """立刻关显示器；动鼠标/键盘就亮，进程不受影响。"""
    try:
        subprocess.run([PY, str(ROOT / "scripts" / "screen_off.py")], timeout=15, check=True)
        return JSONResponse({"ok": True})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)})


# ---------------- 音乐（网易云收藏） ----------------
class NeteaseCookieIn(BaseModel):
    cookie: str


class NeteaseTargetIn(BaseModel):
    id: int
    name: str = ""


def _netease_target() -> dict:
    try:
        return json.loads(NETEASE_TARGET.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@app.get("/api/netease")
def api_netease() -> JSONResponse:
    if netease is None:
        return JSONResponse({"status": {"ok": False, "error": "netease 模块加载失败"}, "playlists": [], "target": {}})
    st = netease.account()
    pls = netease.playlists(st["uid"]) if st.get("ok") else []
    collected = 0
    if netease.COLLECTED.exists():
        collected = sum(1 for _ in netease.COLLECTED.open(encoding="utf-8", errors="ignore"))
    return JSONResponse({"status": st, "playlists": pls, "target": _netease_target(), "collected": collected})


@app.post("/api/netease/cookie")
def api_netease_cookie(payload: NeteaseCookieIn) -> JSONResponse:
    if netease is None:
        return JSONResponse({"ok": False, "error": "netease 模块加载失败"})
    raw = (payload.cookie or "").strip()
    raw = raw.replace("\r", " ").replace("\n", " ")
    if raw.lower().startswith("cookie:"):
        raw = raw[7:].strip()
    if len(raw) < 20:
        return JSONResponse({"ok": False, "error": "cookie 太短，复制完整一点"})
    netease.save_session({"cookie": raw, "updated": time.time()})
    return JSONResponse(netease.account())


_QR = {"unikey": "", "url": "", "code": 0, "msg": "", "thread": None}


def _qr_worker(unikey: str) -> None:
    """后台轮询扫码状态，页面关掉也能把登录态存下来。"""
    deadline = time.time() + 300
    while time.time() < deadline:
        if _QR.get("unikey") != unikey:
            return
        try:
            r = netease.qr_poll(unikey)
        except Exception as exc:  # noqa: BLE001
            _QR["msg"] = f"轮询出错：{exc}"
            time.sleep(2)
            continue
        code = r.get("code")
        _QR["code"] = code
        _QR["msg"] = r.get("msg") or ("登录成功" if code == 803 else "")
        print(f"[netease] qr code={code} {_QR['msg']}", flush=True)
        if code == 803:
            _QR["msg"] = "登录成功" if r.get("ok") else (r.get("error") or "登录失败")
            return
        if code == 800:
            return
        time.sleep(2)


@app.get("/api/netease/qr")
def api_netease_qr() -> JSONResponse:
    if netease is None:
        return JSONResponse({"ok": False, "error": "netease 模块加载失败"})
    r = netease.qr_start()
    if r.get("ok"):
        _QR.update(unikey=r["unikey"], url=r["url"], code=801, msg="等待扫码", thread=None)
        t = threading.Thread(target=_qr_worker, args=(r["unikey"],), daemon=True)
        t.start()
        _QR["thread"] = t
    return JSONResponse(r)


@app.get("/api/netease/qr/status")
def api_netease_qr_status() -> JSONResponse:
    return JSONResponse({"code": _QR.get("code", 0), "msg": _QR.get("msg", "")})


@app.get("/api/netease/qr.png")
def api_netease_qr_png():
    if not _QR.get("url"):
        raise HTTPException(404, "先获取二维码")
    try:
        import io

        import qrcode

        img = qrcode.make(_QR["url"])
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"生成二维码失败：{exc}")


@app.get("/api/netease/qr/poll")
def api_netease_qr_poll() -> JSONResponse:
    if netease is None or not _QR.get("unikey"):
        return JSONResponse({"code": 0, "msg": "先获取二维码"})
    return JSONResponse(netease.qr_poll(_QR["unikey"]))


@app.post("/api/netease/target")
def api_netease_target(payload: NeteaseTargetIn) -> JSONResponse:
    NETEASE_TARGET.parent.mkdir(parents=True, exist_ok=True)
    NETEASE_TARGET.write_text(
        json.dumps({"id": payload.id, "name": payload.name}, ensure_ascii=False), encoding="utf-8"
    )
    return JSONResponse({"ok": True, "target": {"id": payload.id, "name": payload.name}})


@app.get("/api/netease/collected")
def api_netease_collected(limit: int = 100) -> JSONResponse:
    items = []
    if netease and netease.COLLECTED.exists():
        rows = netease.COLLECTED.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line in rows[-max(1, min(limit, 500)):]:
            try:
                items.append(json.loads(line))
            except ValueError:
                continue
        items.reverse()
    return JSONResponse({"items": items, "target": _netease_target()})


# ---------------- 记忆 ----------------
LOG_SEEN = ROOT / "panel" / "log_seen.json"


def _log_error_lines(limit: int = 200) -> list[str]:
    try:
        lines = (ROOT / "logs" / "astrbot.out.log").read_text(
            encoding="utf-8", errors="ignore"
        ).splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        if "[ERRO]" in line or "Traceback" in line:
            out.append(re.sub(r"\x1b\[[0-9;]*m", "", line).strip()[:200])
    return out


def _log_seen_key() -> str:
    try:
        data = json.loads(LOG_SEEN.read_text(encoding="utf-8"))
        return str(data.get("key", ""))
    except (OSError, ValueError):
        return ""


def _new_log_errors() -> list[str]:
    errors = _log_error_lines()
    seen = _log_seen_key()
    if seen:
        last = -1
        for i, text in enumerate(errors):
            if text == seen:
                last = i
        if last >= 0:
            return errors[last + 1:]
    return errors


@app.get("/api/badges")
def api_badges() -> JSONResponse:
    """侧边栏小红点：待处理好友、待确认草稿、未读日志错误。"""
    errors = _new_log_errors()
    drafts = 0
    try:
        drafts = len(json.loads(AI_DRAFTS.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return JSONResponse({
        "空间": drafts,
        "日志": len(errors),
        "last_error": errors[-1] if errors else "",
    })


@app.post("/api/logs/seen")
def api_logs_seen() -> JSONResponse:
    """把当前日志里的错误标成已读，红点只提醒之后的新错误。"""
    cleared = len(_new_log_errors())
    errors = _log_error_lines()
    if errors:
        LOG_SEEN.write_text(
            json.dumps({"key": errors[-1]}, ensure_ascii=False), encoding="utf-8"
        )
    return JSONResponse({"cleared": cleared})


@app.get("/api/stickers")
def stickers() -> JSONResponse:
    out = {}
    if STICKERS.exists():
        for d in STICKERS.iterdir():
            if d.is_dir() and not d.name.startswith("_") and d.name != "incoming":
                files = [p.name for p in d.iterdir()
                         if p.suffix.lower() in {".png", ".jpg", ".gif", ".webp"}]
                out[d.name] = sorted(files)
    return JSONResponse(out)


@app.post("/api/stickers/upload")
async def sticker_upload(mood: str = Form(...), file: UploadFile = File(...)) -> JSONResponse:
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "", mood or "").strip() or "开心"
    ext = pathlib.Path(file.filename or "x.png").suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        raise HTTPException(400, "只收图片")
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, "图太大了")
    folder = STICKERS / safe
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{int(time.time() * 1000)}_{random.randint(100, 999)}{ext}"
    (folder / name).write_bytes(data)
    return JSONResponse({"ok": True, "mood": safe, "file": name})


@app.get("/api/sticker/{mood}/{name}")
def sticker_file(mood: str, name: str):
    path = (STICKERS / mood / name).resolve()
    if not str(path).startswith(str(STICKERS.resolve())) or not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/api/search")
def search(q: str) -> JSONResponse:
    try:
        r = requests.get("https://www.bing.com/search", params={"q": q, "setlang": "zh-CN"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        blocks = re.findall(r'<li class="b_algo".*?</li>', r.text, re.S)
        items = []
        for b in blocks[:5]:
            m = re.search(r'<h2[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
            if m:
                items.append({"title": re.sub(r"<[^>]+>", "", m.group(2)).strip(), "url": m.group(1)})
        return JSONResponse({"ok": True, "items": items})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)})


# ---------------- 音色模型 ----------------
def _wav_seconds(p: pathlib.Path) -> float:
    try:
        import wave

        with wave.open(str(p)) as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:  # noqa: BLE001
        return 0.0


def _emotion_roots(folder: pathlib.Path) -> list[pathlib.Path]:
    """在模型目录（以及 datasets/<名字>_emotion）里找带情绪子文件夹的素材目录。"""
    pools = [folder]
    extra = ROOT / "datasets" / f"{folder.name}_emotion"
    if extra.is_dir():
        pools.append(extra)
    out: list[pathlib.Path] = []
    for pool in pools:
        candidates = [pool] + [d for d in pool.rglob("*") if d.is_dir()]
        for d in candidates[:60]:
            try:
                subs = [x for x in d.iterdir() if x.is_dir()]
            except Exception:  # noqa: BLE001
                continue
            hit = sum(
                1
                for x in subs
                if any(k.lower() in x.name.lower() for k in EMOTION_DIR_NAMES)
            )
            if hit >= 2:
                out.append(d)
    return out


def _pick_ref(folder: pathlib.Path) -> tuple[str, str]:
    """挑一条 3~10 秒的 wav 当参考，返回 (路径, 台词)。"""
    wavs = [p for p in folder.rglob("*.wav") if p.stat().st_size > 4000]
    good = [p for p in wavs if 3.0 <= _wav_seconds(p) <= 10.0]
    for p in good:
        prompt = p.stem
        prompt = re.sub(r"^[【\[][^】\]]*[】\]]", "", prompt).strip(" 　…·-—")
        if prompt:
            return str(p), prompt
    return "", ""


def scan_models() -> list[dict]:
    if not MODEL_ROOT.is_dir():
        return []
    models = []
    for folder in sorted({p.parent for p in MODEL_ROOT.rglob("*.ckpt")}):
        ckpts = sorted(folder.glob("*.ckpt"))
        pths = sorted(folder.glob("*.pth"))
        if not ckpts or not pths:
            continue
        gpt = ckpts[-1]

        def _epoch(p: pathlib.Path) -> int:
            m = re.search(r"[_eE](\d+)", p.name)
            return int(m.group(1)) if m else 0

        # 同名权重优先，其次挑训练轮数最高的
        stem = gpt.stem.split("-e")[0].lower()
        same = [p for p in pths if p.stem.lower().startswith(stem)] or pths
        sov = max(same, key=_epoch)
        roots = _emotion_roots(folder)
        ref, prompt = _pick_ref(folder)
        # 文件名里带 _l32/_l128 的是大模型，合成明显更慢
        heavy = "_l" in sov.stem.lower()
        models.append(
            {
                "name": folder.name,
                "heavy": heavy,
                "folder": str(folder),
                "gpt": str(gpt),
                "sovits": str(sov),
                "emotion_root": str(roots[0]) if roots else "",
                "ref_audio": ref,
                "prompt": prompt,
            }
        )
    return models



# ---------------- 维护模式 ----------------
MAINT_OPEN_DEFAULT = "唔，本鱼去维护一下，弄好了就回来。"
MAINT_CLOSE_DEFAULT = "本鱼回来了，刚刚弄完。"


def _maint_text(value, default: str) -> str:
    text = (value or "").strip()
    if not text or set(text) <= {"?"}:
        return default
    return text


@app.get("/api/maintenance")
def api_maintenance() -> JSONResponse:
    sec = (_bot_cfg().get("modules") or {}).get("maintenance") or {}
    return JSONResponse({
        "enabled": bool(sec.get("enabled")),
        "message": _maint_text(sec.get("message"), "唔，本鱼正在升级，暂时没办法好好聊天，等我弄好了再叫你。"),
        "open_message": _maint_text(sec.get("open_message"), MAINT_OPEN_DEFAULT),
        "close_message": _maint_text(sec.get("close_message"), MAINT_CLOSE_DEFAULT),
    })


class MaintIn(BaseModel):
    enabled: bool | None = None
    message: str | None = None
    open_message: str | None = None
    close_message: str | None = None


@app.post("/api/maintenance")
def api_maintenance_set(payload: MaintIn) -> JSONResponse:
    cfg = _bot_cfg()
    mods = cfg.setdefault("modules", {})
    sec = mods.setdefault("maintenance", {})
    was = bool(sec.get("enabled"))
    if payload.enabled is not None:
        sec["enabled"] = bool(payload.enabled)
    if payload.message is not None:
        sec["message"] = payload.message.strip()
    if payload.open_message is not None:
        sec["open_message"] = payload.open_message.strip()
    if payload.close_message is not None:
        sec["close_message"] = payload.close_message.strip()
    BOT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    BOT_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    now = bool(sec.get("enabled"))
    if payload.enabled is not None and now != was:
        admin = _cfg_str("admin_qq")
        if admin:
            text = (_maint_text(sec.get("open_message"), MAINT_OPEN_DEFAULT) if now else
                    _maint_text(sec.get("close_message"), MAINT_CLOSE_DEFAULT))
            try:
                ob_call("send_private_msg", {
                    "user_id": int(admin),
                    "message": [{"type": "text", "data": {"text": text}}],
                })
            except Exception:  # noqa: BLE001
                pass
    return JSONResponse({"ok": True, "enabled": now})


# ---------------- 路径配置 ----------------
def _bot_cfg() -> dict:
    try:
        return json.loads(BOT_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _paths_cfg() -> dict:
    return _bot_cfg().get("paths") or {}


def _first_existing(*cands) -> str:
    for c in cands:
        try:
            if c and pathlib.Path(str(c)).exists():
                return str(c)
        except OSError:
            continue
    return ""


def _detect_paths() -> dict:
    cur = _paths_cfg()
    rvc_root = _first_existing(
        cur.get("rvc_root"), ROOT / "rvc", r"D:\RVC", r"D:\rvc"
    )
    rvc_pkg = ""
    if rvc_root:
        hits = sorted(pathlib.Path(rvc_root).glob("pkg/*/runtime/python.exe"))
        if hits:
            rvc_pkg = str(hits[0].parent.parent)
    model_map = _first_existing(
        cur.get("model_map"),
        pathlib.Path(rvc_root or ROOT / "rvc") / "model_map.json",
    )
    return {
        "tts_root": _first_existing(
            cur.get("tts_root"),
            os.environ.get("GPT_MODEL_ROOT"),
            ROOT / "models" / "tts",
            r"D:\GPT-SoVITS\models",
            r"D:\GPT-SoVITS",
        ),
        "rvc_root": rvc_root,
        "rvc_pkg": rvc_pkg or _first_existing(cur.get("rvc_pkg")),
        "model_map": model_map,
        "ffmpeg": _first_existing(cur.get("ffmpeg"), ROOT / "bin" / "ffmpeg.exe"),
    }


@app.get("/api/paths")
def api_paths() -> JSONResponse:
    return JSONResponse(
        {"current": _paths_cfg(), "detected": _detect_paths(), "root": str(ROOT)}
    )


class PathsIn(BaseModel):
    tts_root: str = ""
    rvc_root: str = ""
    rvc_pkg: str = ""
    model_map: str = ""
    ffmpeg: str = ""


@app.get("/api/paths/check")
def api_paths_check(key: str, value: str = "") -> JSONResponse:
    allowed = {"tts_root", "rvc_root", "rvc_pkg", "model_map", "ffmpeg"}
    if key not in allowed:
        raise HTTPException(400, "key 不对")
    value = (value or "").strip()
    return JSONResponse({"exists": bool(value) and pathlib.Path(value).exists()})


@app.post("/api/paths")
def api_paths_set(payload: PathsIn) -> JSONResponse:
    values = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    cfg = _bot_cfg()
    cfg["paths"] = {k: v for k, v in values.items() if v}
    BOT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    BOT_CONFIG.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return JSONResponse({"ok": True, "msg": "路径已保存，重启 AstrBot 生效"})


@app.get("/api/models")
def api_models() -> JSONResponse:
    global MODEL_ROOT
    configured = _paths_cfg().get("tts_root")
    if configured:
        MODEL_ROOT = pathlib.Path(configured)
    try:
        current = json.loads(MODEL_STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        current = {}
    return JSONResponse({"root": str(MODEL_ROOT), "current": current, "models": scan_models()})


class ModelSwitch(BaseModel):
    gpt: str
    sovits: str = ""
    name: str = ""
    emotion_root: str = ""
    ref_audio: str = ""
    prompt: str = ""


@app.post("/api/models/switch")
def api_model_switch(payload: ModelSwitch) -> JSONResponse:
    gpt = pathlib.Path(payload.gpt)
    if not gpt.exists():
        raise HTTPException(400, f"找不到权重：{gpt}")
    if not payload.sovits:
        cands = sorted(p.parent.glob("*.pth"))
        if not cands:
            raise HTTPException(400, "该目录下没有 SoVITS 权重")
        payload.sovits = str(cands[-1])
    try:
        r1 = requests.get(f"{GSV_URL}/set_gpt_weights", params={"weights_path": payload.gpt}, timeout=300)
        r2 = requests.get(f"{GSV_URL}/set_sovits_weights", params={"weights_path": payload.sovits}, timeout=300)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"GPT-SoVITS 服务连不上：{exc}")
    if r1.status_code != 200 or r2.status_code != 200:
        raise HTTPException(502, f"切换失败：{r1.text[:80]} / {r2.text[:80]}")

    # 记下当前选择，重启后自动恢复
    emo_root = payload.emotion_root
    if not emo_root:
        roots = _emotion_roots(gpt.parent)
        emo_root = str(roots[0]) if roots else ""
    if not emo_root:
        # 自动分类已停用：情绪素材由人工按文件夹整理，这里不再自己生成
        emo_root = ""
    ref = payload.ref_audio
    prompt = payload.prompt
    if not ref:
        ref, prompt = _pick_ref(gpt.parent)
    state = {
        "name": payload.name or gpt.parent.name,
        "gpt": payload.gpt,
        "sovits": payload.sovits,
        "emotion_root": emo_root,
        "ref_audio": ref,
        "prompt": prompt,
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    MODEL_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    # 顺便把语音插件的参考素材指到这个模型的情绪素材（如果有）
    plugin_updated = False
    try:
        cfg = json.loads(VOICE_CFG.read_text(encoding="utf-8-sig"))
        root = payload.emotion_root
        if not root:
            roots = _emotion_roots(gpt.parent)
            root = str(roots[0]) if roots else ""
        if not root:
            # 自动分类已停用：找不到就用模型目录里已有的情绪文件夹，不做新分类
            root = ""
        if root:
            cfg["emotion_ref_root"] = root
            cfg["emotion_ref_enable"] = True
            cfg["emotion_ref_lang"] = "zh"
            plugin_updated = True
        else:
            # 找不到任何情绪素材时关掉，避免误用上一个模型的素材
            cfg["emotion_ref_enable"] = False
            cfg["emotion_ref_root"] = ""
            plugin_updated = True
        ref = payload.ref_audio
        prompt = payload.prompt
        if not ref:
            ref, prompt = _pick_ref(gpt.parent)
        if ref:
            cfg["gsv_ref_audio"] = ref
            cfg["gsv_prompt_text"] = prompt
            cfg["gsv_prompt_lang"] = "zh"
            plugin_updated = True
        if plugin_updated:
            VOICE_CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=4), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    return JSONResponse({"ok": True, "state": state, "plugin_updated": plugin_updated})


@app.get("/backups", response_class=HTMLResponse)
def backups_page() -> HTMLResponse:
    items = _backup_items(30)
    rows = []
    for item in items:
        when = datetime.datetime.fromtimestamp(item["mtime"]).strftime("%Y-%m-%d %H:%M")
        size_mb = item["size"] / 1024 / 1024
        size = f"{size_mb:.1f} MB" if size_mb >= 1 else f"{max(1, round(item['size'] / 1024))} KB"
        name = html.escape(item["name"])
        rows.append(
            f'<tr><td><a href="/backups/{name}">{name}</a></td>'
            f"<td>{size}</td><td>{when}</td>"
            f'<td><a class="dl" href="/backups/{name}">下载</a></td></tr>'
        )
    if not rows:
        rows.append('<tr><td colspan="4" class="muted">还没有备份，点上面的按钮跑一次。</td></tr>')

    head = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>大肥鱼备份</title>
<style>
body{margin:0;background:#0f1420;color:#dbe4f5;font:14px/1.6 "Microsoft YaHei",system-ui,sans-serif}
.wrap{max-width:860px;margin:40px auto;padding:0 18px}
h1{font-size:22px;margin:0 0 6px}
.sub{color:#8b9bb8;font-size:13px;margin:0 0 18px}
.row{display:flex;gap:10px;align-items:center;margin-bottom:18px}
button,a{color:#5b9cff}
button{background:#22304a;color:#dbe4f5;border:1px solid #253047;border-radius:8px;padding:7px 14px;cursor:pointer;font-size:13px}
button:hover{border-color:#5b9cff}
table{width:100%;border-collapse:collapse;background:#171e2e;border:1px solid #253047;border-radius:12px;overflow:hidden}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid #253047}
th{color:#8b9bb8;font-weight:500;font-size:12px}
tr:last-child td{border-bottom:0}
a{text-decoration:none}
a:hover{text-decoration:underline}
.dl{white-space:nowrap}
.muted{color:#8b9bb8}
</style></head><body><div class="wrap">
<h1>大肥鱼备份</h1>
"""
    summary = (
        f'<p class="sub">每天 03:00 自动备份，保留最近 14 份每日备份。当前共 '
        f"<b>{_backup_count()}</b> 份，部署包另列。最近一份可以直接点下载。</p>"
        '<div class="row"><button onclick="startBackup(this)">立即备份</button>'
        '<a href="/">返回控制台</a></div>'
    )
    table = (
        "<table><thead><tr><th>文件</th><th>大小</th><th>时间</th><th></th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    tail = '<p class="sub" style="margin-top:16px">备份在 D:\\QQBot\\backups，包含 AstrBot 数据、大肥鱼数据、SnowLuma 配置、密钥、人设和音色状态。</p>'
    tail += """<script>
async function startBackup(btn){
  btn.disabled=true; btn.textContent='备份中…';
  try{ await fetch('/api/backup',{method:'POST'}); }catch(e){}
  const timer=setInterval(async()=>{
    try{
      const d=await (await fetch('/api/backup/status')).json();
      if(!d.running){ clearInterval(timer); location.reload(); }
    }catch(e){}
  },2000);
}
</script>"""
    tail += "</div></body></html>"
    return HTMLResponse(head + summary + table + tail, headers={"Cache-Control": "no-store"})


@app.get("/backups/{name}")
def backup_download(name: str):
    if not re.fullmatch(r"qqbot-[A-Za-z0-9_.-]+\.zip", name):
        raise HTTPException(404, "not found")
    path = BACKUP_DIR / name
    if not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(str(path), media_type="application/zip", filename=name)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = (pathlib.Path(__file__).parent / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(
        html,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


def _panel_singleton():
    """控制台只允许开一个，避免两个进程互相抢着写状态。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind(("127.0.0.1", 6869))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def _patch_guard_check() -> None:
    script = ROOT / "scripts" / "patch_guard.py"
    if not script.exists():
        return
    try:
        r = subprocess.run(
            [PY, str(script)], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=40,
            creationflags=0x08000000,
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        try:
            (ROOT / "logs" / "patch_guard.log").write_text(out + "\n", encoding="utf-8")
        except OSError:
            pass
        if r.returncode != 0:
            admin = _cfg_str("admin_qq")
            if admin:
                ob_call("send_private_msg", {
                    "user_id": int(admin),
                    "message": [{"type": "text", "data": {"text": "补丁检查发现问题：\n" + out[:400]}}],
                })
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    _panel_lock = _panel_singleton()
    if _panel_lock is None:
        sys.exit(0)
    threading.Thread(target=_patch_guard_check, daemon=True).start()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=6868, log_level="warning")

