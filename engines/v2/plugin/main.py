import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from dafeiyu_core.kernel import Kernel


@register("astrbot_plugin_dafeiyu", "dafeiyu", "大肥鱼", "2.0.0")
class DafeiyuPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        self.kernel = Kernel(self, context)

    async def initialize(self) -> None:
        try:
            await self.kernel.setup()
        except Exception:  # noqa: BLE001
            logger.error(f"[dafeiyu] 启动失败\n{traceback.format_exc()}")

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def on_message(self, event: AstrMessageEvent) -> None:
        await self.kernel.dispatch("on_message", event)

    @filter.on_waiting_llm_request()
    async def on_waiting_llm_request(self, event: AstrMessageEvent) -> None:
        await self.kernel.dispatch("on_waiting_llm_request", event)

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req) -> None:
        await self.kernel.dispatch("on_llm_request", event, req)

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, response) -> None:
        await self.kernel.dispatch("on_llm_response", event, response)

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent) -> None:
        await self.kernel.dispatch("on_decorating_result", event)
