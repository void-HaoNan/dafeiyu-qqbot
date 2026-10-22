"""黑白名单守门：黑名单直接不理，白名单外的人和新拉进的群都不理。

名单是数据，存在 data/access_lists.json，控制台读写；守门逻辑在这个文件里。
好友申请不在这里处理，直接在 QQ 里点同意；新人新群要手动加白名单才会理。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from astrbot.api import logger

from dafeiyu_core.module import Module, register

try:
    from dafeiyu_core.paths import ROOT
except Exception:  # noqa: BLE001
    ROOT = Path(os.environ.get("QQBOT_ROOT", r"D:\QQBot"))

STORE = ROOT / "data" / "access_lists.json"

DEFAULT = {
    "mode": "whitelist",
    "allow_all_groups": False,
    "auto_whitelist_new_friend": False,
    "whitelist": {"users": [], "groups": []},
    "blacklist": {"users": [], "groups": []},
}


def load() -> dict:
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    for k, v in DEFAULT.items():
        data.setdefault(k, v)
    for side in ("whitelist", "blacklist"):
        data.setdefault(side, {"users": [], "groups": []})
        data[side].setdefault("users", [])
        data[side].setdefault("groups", [])
    return data


@register
class AccessModule(Module):
    name = "access"
    description = "黑白名单守门：白名单外的人和群一律不理"
    priority = 10

    async def setup(self) -> None:
        self._admin = str(os.environ.get("DAFEIYU_ADMIN_QQ", ""))
        try:
            self._admin = str(self.kernel.cfg.get("admin_qq") or self._admin)
        except Exception:  # noqa: BLE001
            pass

    def _load_requests(self) -> list[dict]:
        try:
            return json.loads(REQ_STORE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save_requests(self, items: list[dict]) -> None:
        REQ_STORE.parent.mkdir(parents=True, exist_ok=True)
        REQ_STORE.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")

    async def on_message(self, event) -> None:
        await self._guard(event)

    async def _guard(self, event) -> None:
        try:
            uid = str(event.get_sender_id() or "")
            gid = str(event.get_group_id() or "")
            if uid == self._admin:
                return
            data = load()
            bl = data["blacklist"]
            if uid in bl["users"] or (gid and gid in bl["groups"]):
                logger.info(f"[access] 黑名单拦截 user={uid} group={gid}")
                event.stop_event()
                return
            if data.get("mode") != "whitelist":
                return
            wl = data["whitelist"]
            if uid in wl["users"] or (gid and gid in wl["groups"]):
                return
            logger.info(f"[access] 不在白名单拦截 user={uid} group={gid}")
            event.stop_event()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[access] 名单检查失败（放行）: {exc}")
