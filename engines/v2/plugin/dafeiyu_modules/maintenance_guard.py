from __future__ import annotations

import json
import os
import pathlib
import time

from astrbot.api import logger

from dafeiyu_core.module import Module, register

try:
    from dafeiyu_core.paths import ROOT
except Exception:  # noqa: BLE001
    ROOT = pathlib.Path(os.environ.get("QQBOT_ROOT", r"D:\QQBot"))

BUFFER = ROOT / "data" / "dafeiyu" / "maintenance_buffer.json"


def _load() -> dict:
    try:
        data = json.loads(BUFFER.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    try:
        BUFFER.parent.mkdir(parents=True, exist_ok=True)
        tmp = BUFFER.with_name(f"maintenance_buffer.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, BUFFER)
    except OSError:
        pass


@register
class MaintenanceGuard(Module):
    """维护模式：插件自己回一句维护提示，压住所有正常回复，并缓存消息给恢复后接上下文。"""

    name = "maintenance_guard"
    description = "维护模式时由插件回维护提示、停止正常回复，并缓存期间消息"
    priority = 1

    def __init__(self, kernel):
        super().__init__(kernel)
        self._last: dict[str, float] = {}

    def _notify(self, event, sec: dict, gid: str, uid: str) -> None:
        key = gid or f"u{uid}"
        now = time.time()
        cooldown = float(sec.get("cooldown_seconds", 3) or 0)
        if cooldown > 0 and now - self._last.get(key, 0) < cooldown:
            return
        text = sec.get("message") or "唔，本鱼正在升级，暂时没办法好好聊天，等我弄好了再叫你。"
        message = [{"type": "text", "data": {"text": text}}]
        try:
            if gid:
                self.kernel.bot.call("send_group_msg", group_id=int(gid), message=message)
            else:
                self.kernel.bot.call("send_private_msg", user_id=int(uid), message=message)
            self._last[key] = now
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[maintenance] 发提示失败: {exc}")

    async def on_message(self, event) -> None:
        self.kernel.cfg.reload_if_changed()
        sec = self.kernel.cfg.module_cfg("maintenance") or {}
        if not sec.get("enabled"):
            return
        gid = str(event.get_group_id() or "")
        uid = str(event.get_sender_id() or "")
        if uid:
            self._notify(event, sec, gid, uid)
        key = gid if gid else f"u{uid}"
        text = (event.get_message_str() or "").strip()
        if key and text:
            data = _load()
            rows = data.get(key) or []
            rows.append({"name": event.get_sender_name() or uid, "text": text[:200]})
            data[key] = rows[-30:]
            _save(data)
        event.stop_event()
