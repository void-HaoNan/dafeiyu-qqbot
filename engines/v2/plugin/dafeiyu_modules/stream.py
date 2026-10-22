import asyncio
import time

from astrbot.api.message_components import Image

from dafeiyu_core import history, stream
from dafeiyu_core.module import Module, register


@register
class StreamModule(Module):
    name = "stream"
    description = "把收发消息写进 logs/panel_stream.jsonl，供控制台显示"

    async def setup(self) -> None:
        self._cleaned = {}

    async def _clean_history(self, umo: str) -> None:
        await asyncio.sleep(3)
        saved = await asyncio.to_thread(history.strip_images, umo)
        if saved:
            print(f"[history] 清掉原图数据 {saved // 1024} KB", flush=True)

    async def on_message(self, event) -> None:
        try:
            text = event.get_message_str() or ""
        except Exception:  # noqa: BLE001
            text = ""
        try:
            images = [c for c in event.get_messages() if isinstance(c, Image)]
        except Exception:  # noqa: BLE001
            images = []
        if text or images:
            stream.write(event, text or "[图片]", "in", len(images))

    async def on_decorating_result(self, event) -> None:
        result = event.get_result()
        if result is None:
            return
        text = ""
        getter = getattr(result, "get_plain_text", None)
        if callable(getter):
            try:
                text = getter() or ""
            except Exception:  # noqa: BLE001
                text = ""
        images = 0
        try:
            chain = result.chain or []
            images = sum(1 for c in chain if isinstance(c, Image))
        except Exception:  # noqa: BLE001
            images = 0
        if text or images:
            stream.write(event, text or "[图片]", "out", images)
        umo = event.unified_msg_origin
        now = time.time()
        if now - self._cleaned.get(umo, 0) > 5:
            self._cleaned[umo] = now
            asyncio.create_task(self._clean_history(umo))
