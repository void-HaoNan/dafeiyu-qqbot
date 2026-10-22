"""群聊接话判断：全部都唤醒，但先让模型判断这句是不是在找本鱼。

reply_all 开着时 AstrBot 会为每条群消息起一轮回复。这里在进主模型之前先问一次
很短的分类问题，判断「这句要不要接」：不接就停掉事件，主模型和工具都不会跑。
"""

from __future__ import annotations

import json
import os
import pathlib
import time
import uuid

from astrbot.api import logger

from dafeiyu_core.module import Module, register

try:
    from dafeiyu_core.paths import ROOT
except Exception:  # noqa: BLE001
    ROOT = pathlib.Path(os.environ.get("QQBOT_ROOT", r"D:\QQBot"))

STREAM = ROOT / "logs" / "panel_stream.jsonl"
ALIASES = ("大肥鱼", "鱼鱼", "本鱼", "鲸鱼娘")
MAX_HISTORY = 8
MIN_GAP = 15


@register
class ReplyGate(Module):
    name = "reply_gate"
    description = "群聊里先判断这句是不是在找本鱼，不是就不接"
    priority = 5

    async def setup(self) -> None:
        self._admin = ""
        try:
            self._admin = str(self.kernel.cfg.get("admin_qq") or "")
        except Exception:  # noqa: BLE001
            pass
        logger.info("[reply_gate] 接话判断已加载")

    async def on_message(self, event) -> None:
        gid = str(event.get_group_id() or "")
        if not gid:
            return
        try:
            if (self.kernel.cfg.module_cfg("maintenance") or {}).get("enabled"):
                return
        except Exception:  # noqa: BLE001
            pass
        text = (event.get_message_str() or "").strip()
        try:
            from astrbot.api.message_components import At, Reply

            for comp in event.get_messages():
                if isinstance(comp, At) and str(comp.qq) == str(event.get_self_id()):
                    return
                if isinstance(comp, Reply) and str(comp.sender_id) == str(event.get_self_id()):
                    return
        except Exception:  # noqa: BLE001
            pass
        if not text:
            return
        if text.startswith("/") or any(a in text for a in ALIASES):
            return
        verdict = await self._ask(event, gid, text)
        if verdict is None:
            verdict = 1
        if verdict <= 0:
            event.stop_event()
            return
        gap = self._last_bot_gap(gid)
        if gap is not None and gap < MIN_GAP and verdict < 2:
            event.stop_event()

    def _last_bot_gap(self, gid: str) -> float | None:
        try:
            lines = STREAM.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
        except OSError:
            return None
        now = time.time()
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if str(rec.get("conv_id") or "") != gid or rec.get("dir") != "out":
                continue
            return now - float(rec.get("ts") or 0)
        return None

    def _history(self, gid: str) -> str:
        try:
            lines = STREAM.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
        except OSError:
            return ""
        rows = []
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if str(rec.get("conv_id") or "") != gid or not rec.get("is_group"):
                continue
            text = (rec.get("text") or "").strip()
            if not text:
                continue
            who = "大肥鱼" if rec.get("dir") == "out" else (rec.get("sender") or "群友")
            rows.append(f"{who}：{text[:120]}")
        return "\n".join(rows[-MAX_HISTORY:])

    async def _ask(self, event, gid: str, text: str) -> int | None:
        try:
            provider = await self.kernel.context.get_using_provider_async()
            if not provider:
                return None
            history = self._history(gid)
            prompt = (
                "你在看一个QQ群聊。机器人叫「大肥鱼」，也叫「鱼鱼」「本鱼」。\n"
                f"最近几条：\n{history or '（没有）'}\n\n"
                f"现在 {event.get_sender_name() or '群友'} 说：{text}\n\n"
                "像真群友一样判断大肥鱼现在想不想接这句，不要每句都接。\n"
                "0 = 不接：插不上、没兴趣、别人聊得正热\n"
                "1 = 可以接：有点想说的，但不强求\n"
                "2 = 很想接：冷场了、有人提问、话题正好是它喜欢的、能接梗或想吐槽\n"
                "只回一个数字 0、1 或 2。"
            )
            resp = await provider.text_chat(
                prompt=prompt,
                session_id=uuid.uuid4().hex,
                system_prompt="你只输出 0、1 或 2，不要解释。",
                persist=False,
            )
            answer = (getattr(resp, "completion_text", "") or "").strip()
            for ch in answer:
                if ch in "012":
                    return int(ch)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[reply_gate] 判断失败，先接住: {exc}")
        return None
