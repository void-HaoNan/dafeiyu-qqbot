"""表情包模块：按情绪发图，顺手把收到的图存给控制台。

不再依赖 _impl，发图、收图、消息流都在这个文件里。
"""

import asyncio
import base64
import json
import pathlib
import random
import shutil
import time

import requests

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image

from dafeiyu_core.module import Module, ToolSpec, register

try:
    from dafeiyu_core.paths import ROOT
except Exception:  # noqa: BLE001
    import os

    ROOT = pathlib.Path(os.environ.get("QQBOT_ROOT", r"D:\QQBot"))

STICKER_ROOT = ROOT / "stickers"
STREAM = ROOT / "logs" / "panel_stream.jsonl"
IMG_DIR = ROOT / "logs" / "panel_images"
IMG_INDEX = IMG_DIR / "index.jsonl"
EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_KEEP = 400

ALIASES = {
    "开心": ("happy", "开心", "高兴"),
    "生气": ("angry", "生气", "愤怒"),
    "难过": ("sad", "难过", "伤心"),
    "害羞": ("shy", "害羞"),
    "撒娇": ("coquettish", "撒娇"),
    "疑惑": ("confused", "疑惑"),
    "委屈": ("grievance", "委屈"),
    "激动": ("excited", "激动"),
    "惊讶": ("surprised", "惊讶"),
    "饿": ("hungry", "饿"),
}


def moods() -> list[str]:
    if not STICKER_ROOT.is_dir():
        return []
    return [d.name for d in STICKER_ROOT.iterdir() if d.is_dir() and d.name != "incoming"]


def _resolve(mood: str) -> pathlib.Path | None:
    dirs = moods()
    if not dirs:
        return None
    mood = (mood or "").strip()
    if mood in dirs:
        return STICKER_ROOT / mood
    for alias in ALIASES.get(mood, ()):
        for d in dirs:
            if alias.lower() in d.lower():
                return STICKER_ROOT / d
    for d in dirs:
        if mood and (mood.lower() in d.lower() or d.lower() in mood.lower()):
            return STICKER_ROOT / d
    return None


def _pick(mood: str, avoid: str = "") -> pathlib.Path | None:
    folder = _resolve(mood)
    if folder is None or not folder.is_dir():
        return None
    files = [p for p in folder.rglob("*") if p.suffix.lower() in EXTS]
    if not files:
        return None
    if avoid and len(files) > 1:
        files = [p for p in files if p.name != avoid] or files
    return random.choice(files)


def _stash_image(event, src: pathlib.Path) -> str:
    try:
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(time.time() * 1000)}_{random.randint(100, 999)}{src.suffix.lower()}"
        shutil.copy2(src, IMG_DIR / name)
        _write_index({
            "ts": time.time(),
            "umo": event.unified_msg_origin,
            "sender": "大肥鱼",
            "uid": str(event.get_sender_id() or ""),
            "file": name,
        })
        return name
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[face] 存图给面板失败: {exc}")
        return ""


def _save_bytes(data: bytes, ext: str) -> pathlib.Path:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{int(time.time() * 1000)}_{random.randint(100, 999)}.{ext}"
    path = IMG_DIR / name
    path.write_bytes(data)
    return path


def _write_index(rec: dict) -> None:
    with IMG_INDEX.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    try:
        lines = IMG_INDEX.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_KEEP:
            IMG_INDEX.write_text("\n".join(lines[-MAX_KEEP:]) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _log_stream(event, text: str, images: int) -> None:
    try:
        gid = str(event.get_group_id() or "")
        rec = {
            "ts": time.time(),
            "dir": "out",
            "conv_id": gid or str(event.get_sender_id() or ""),
            "is_group": bool(gid),
            "sender": "大肥鱼",
            "uid": str(event.get_sender_id() or ""),
            "umo": event.unified_msg_origin,
            "text": text,
            "images": images,
        }
        with STREAM.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[face] 写消息流失败: {exc}")


@register
class FaceModule(Module):
    name = "face"
    description = "表情包：按情绪发图，也把收到的图存给控制台"

    async def setup(self) -> None:
        self._last = ""
        logger.info(f"[face] 可用情绪：{', '.join(moods()) or '（没有文件夹）'}")

    def tools(self):
        return [
            ToolSpec(
                "send_sticker",
                "发一张表情包。聊天气氛到了、或者对方情绪明显时调用，不要每句都发。",
                {
                    "type": "object",
                    "properties": {
                        "mood": {
                            "type": "string",
                            "description": "情绪：开心 生气 难过 害羞 撒娇 疑惑 委屈 激动 惊讶 饿",
                            "default": "开心",
                        }
                    },
                },
                self.send_sticker,
            )
        ]

    async def send_sticker(self, event, mood: str = "开心"):
        path = _pick(mood, self._last)
        if not path:
            return f"没有「{mood}」这个情绪的表情，可用的是：{'、'.join(moods())}"
        self._last = path.name
        try:
            await event.send(MessageChain([Image.fromFileSystem(str(path))]))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[face] 发送失败: {exc}")
            return f"发送失败：{exc}"
        logger.info(f"[face] 模型调用表情，情绪={mood}，{path.parent.name}/{path.name[:24]}")
        _stash_image(event, path)
        _log_stream(event, f"[表情包·{mood}]", 1)
        return f"已发送一张{mood}系表情"

    async def on_message(self, event) -> None:
        text = (event.get_message_str() or "").strip()
        if text.startswith(("/表情", "／表情")):
            parts = text.split(maxsplit=1)
            mood = parts[1].strip() if len(parts) > 1 else "开心"
            path = _pick(mood, self._last)
            if not path:
                await event.send(MessageChain().message("可用情绪：" + "、".join(moods())))
                event.stop_event()
                return
            self._last = path.name
            await event.send(MessageChain([Image.fromFileSystem(str(path))]))
            _stash_image(event, path)
            _log_stream(event, f"[表情包·{mood}]", 1)
            event.stop_event()
            return
        await self._capture(event)

    async def _capture(self, event) -> None:
        try:
            imgs = [c for c in event.get_messages() if isinstance(c, Image)]
            for comp in imgs:
                saved = await self._grab(comp)
                if not saved:
                    continue
                _write_index({
                    "ts": time.time(),
                    "umo": event.unified_msg_origin,
                    "sender": event.get_sender_name() or "",
                    "uid": str(event.get_sender_id()),
                    "file": saved.name,
                })
                logger.info(f"[face] 收到图片已存 {saved.name}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[face] 收图失败: {exc}")

    async def _grab(self, comp) -> pathlib.Path | None:
        try:
            bs64 = await comp.convert_to_base64()
            if bs64:
                if bs64.startswith("base64://"):
                    bs64 = bs64[len("base64://"):]
                return _save_bytes(base64.b64decode(bs64), "png")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[face] convert_to_base64 失败: {exc}")
        url = getattr(comp, "url", "") or ""
        if url.startswith("http"):
            try:
                r = await asyncio.to_thread(requests.get, url, timeout=20)
                if r.status_code == 200 and r.content:
                    ext = "png" if "png" in url.lower() else "jpg"
                    return _save_bytes(r.content, ext)
            except Exception:  # noqa: BLE001
                pass
        for val in (getattr(comp, "file", ""), getattr(comp, "path", "")):
            if isinstance(val, str) and val.startswith("base64://"):
                try:
                    raw = base64.b64decode(val[len("base64://"):])
                    return _save_bytes(raw, "png")
                except Exception:  # noqa: BLE001
                    pass
        for val in (getattr(comp, "path", ""), getattr(comp, "file", "")):
            if isinstance(val, str) and val:
                p = pathlib.Path(val)
                if p.exists() and p.is_file():
                    try:
                        return _save_bytes(p.read_bytes(), p.suffix.lstrip(".") or "png")
                    except Exception:  # noqa: BLE001
                        pass
                if val.startswith("file://"):
                    p = pathlib.Path(val[7:])
                    if p.exists():
                        try:
                            return _save_bytes(p.read_bytes(), p.suffix.lstrip(".") or "png")
                        except Exception:  # noqa: BLE001
                            pass
        return None
