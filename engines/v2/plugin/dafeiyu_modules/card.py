import json

from dafeiyu_core.module import Module, register

KEYS = ("title", "desc", "singer", "artist", "name", "song")


def _walk(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in KEYS and isinstance(v, str) and v.strip() and len(v) < 80:
                out.append(v.strip())
            _walk(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _walk(item, out)


@register
class CardModule(Module):
    name = "card"
    description = "把 QQ 音乐卡片解析成歌名，补进消息文本"

    async def on_message(self, event) -> None:
        found = []
        for comp in event.get_messages():
            kind = type(comp).__name__
            if kind == "Music":
                found.append(getattr(comp, "title", "") or "")
            elif kind == "Json":
                _walk(getattr(comp, "data", {}) or {}, found)
        if found:
            extra = " ".join(f"[音乐卡片：{x}]" for x in dict.fromkeys(found) if x)
            if extra:
                event.message_str = f"{event.message_str} {extra}".strip()
