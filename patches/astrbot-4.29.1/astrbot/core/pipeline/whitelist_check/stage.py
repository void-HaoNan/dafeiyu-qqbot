from collections.abc import AsyncGenerator

from astrbot.core import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.message_type import MessageType

from ..context import PipelineContext
from ..stage import Stage, register_stage


@register_stage
class WhitelistCheckStage(Stage):
    """检查是否在群聊/私聊白名单"""

    async def initialize(self, ctx: PipelineContext) -> None:
        self.enable_whitelist_check = ctx.astrbot_config["platform_settings"][
            "enable_id_white_list"
        ]
        self.whitelist = ctx.astrbot_config["platform_settings"]["id_whitelist"]
        self.whitelist = [
            str(i).strip() for i in self.whitelist if str(i).strip() != ""
        ]
        self.wl_ignore_admin_on_group = ctx.astrbot_config["platform_settings"][
            "wl_ignore_admin_on_group"
        ]
        self.wl_ignore_admin_on_friend = ctx.astrbot_config["platform_settings"][
            "wl_ignore_admin_on_friend"
        ]
        self.wl_log = ctx.astrbot_config["platform_settings"]["id_whitelist_log"]

    def _reload_if_changed(self) -> None:
        """控制台改名单后立即生效，不用重启 AstrBot。"""
        import json
        from pathlib import Path as _Path

        path = _Path(__file__).resolve().parents[4] / "data" / "cmd_config.json"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
        if mtime == getattr(self, "_cfg_mtime", None):
            return
        self._cfg_mtime = mtime
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        ps = cfg.get("platform_settings") or {}
        self.enable_whitelist_check = bool(ps.get("enable_id_white_list"))
        self.whitelist = [
            str(i).strip() for i in (ps.get("id_whitelist") or []) if str(i).strip()
        ]
        self.wl_log = bool(ps.get("id_whitelist_log", True))

    async def process(
        self,
        event: AstrMessageEvent,
    ) -> None | AsyncGenerator[None, None]:
        self._reload_if_changed()
        if not self.enable_whitelist_check:
            # 白名单检查未启用
            return

        if len(self.whitelist) == 0:
            # 白名单为空，不检查
            return

        if event.get_platform_name() == "webchat":
            # WebChat 豁免
            return

        # 检查是否在白名单
        if self.wl_ignore_admin_on_group:
            if (
                event.role == "admin"
                and event.get_message_type() == MessageType.GROUP_MESSAGE
            ):
                return
        if self.wl_ignore_admin_on_friend:
            if (
                event.role == "admin"
                and event.get_message_type() == MessageType.FRIEND_MESSAGE
            ):
                return
        if (
            event.unified_msg_origin not in self.whitelist
            and str(event.get_group_id()).strip() not in self.whitelist
        ):
            if self.wl_log:
                logger.info(
                    f"Session ID {event.unified_msg_origin} is not in the session "
                    "allowlist, so event propagation was stopped. Add this session "
                    "ID in WebUI: Config -> Access Control -> Whitelist -> "
                    "Whitelist ID List, using the profile assigned to this bot.",
                )
            event.stop_event()
