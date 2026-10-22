"""跨群发消息：管理员让机器人去指定群说一句。

原生实现，不再依赖 _impl；配置读 data/dafeiyu/config.json 的 modules.sendto。
"""

import json
import os
import pathlib
import sys
import time

import requests

from astrbot.api import logger

from dafeiyu_core.module import Module, ToolSpec, register

try:
    from dafeiyu_core.paths import ROOT
except Exception:  # noqa: BLE001
    ROOT = pathlib.Path(os.environ.get("QQBOT_ROOT", r"D:\QQBot"))

SNOW_CFG = next(
    iter(sorted((ROOT / "SnowLuma" / "config").glob("onebot_*.json"))),
    ROOT / "SnowLuma" / "config" / "onebot.json",
)
ONEBOT = "http://127.0.0.1:3000"
DEFAULTS = {"default_group": "", "cooldown_seconds": 5, "max_per_minute": 6}


def _token() -> str:
    try:
        d = json.loads(SNOW_CFG.read_text(encoding="utf-8-sig"))
        return d["networks"]["httpServers"][0]["accessToken"]
    except Exception:  # noqa: BLE001
        return ""


def _groups() -> dict[str, str]:
    try:
        r = requests.post(
            ONEBOT + "/get_group_list",
            headers={"Authorization": f"Bearer {_token()}"},
            json={}, timeout=15,
        )
        data = r.json().get("data") or []
        return {str(g.get("group_name") or ""): str(g.get("group_id")) for g in data}
    except Exception:  # noqa: BLE001
        return {}


@register
class SendToModule(Module):
    name = "sendto"
    description = "跨群发消息"

    async def setup(self) -> None:
        self._last: dict[str, list[float]] = {}

    def conf(self) -> dict:
        out = dict(DEFAULTS)
        out.update(self.kernel.cfg.module_cfg("sendto"))
        return out

    def _is_admin(self, uid: str) -> bool:
        ids = {str(os.environ.get("DAFEIYU_ADMIN_QQ", ""))}
        try:
            ids.add(str(self.kernel.cfg.get("admin_qq") or ""))
            cfg = self.kernel.context.get_config() or {}
            ids.update(str(x) for x in cfg.get("admins_id", []))
        except Exception:  # noqa: BLE001
            pass
        return str(uid) in ids

    def _allowed(self, gid: str) -> tuple[bool, str]:
        c = self.conf()
        now = time.time()
        hist = [t for t in self._last.get(gid, []) if now - t < 60]
        if hist and now - hist[-1] < float(c["cooldown_seconds"]):
            return False, f"刚发过，等 {int(float(c['cooldown_seconds']))} 秒"
        if len(hist) >= int(c["max_per_minute"]):
            return False, "一分钟内发太多了"
        hist.append(now)
        self._last[gid] = hist
        return True, ""

    def tools(self):
        return [
            ToolSpec(
                "send_group_message",
                "往群里发消息。只在私聊里调用；当前消息来自群聊时绝对不要调用，直接用当前会话回复。用户让你往群里发歌就填 song。",
                {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "要发的内容；只发歌时可以留空"},
                        "group": {"type": "string", "description": "群名或群号，留空发默认群"},
                        "song": {"type": "string", "description": "要发到群里的歌名；用户让你往群里发歌/点歌时填这里，会发音乐卡片"},
                    },
                    "required": [],
                },
                self.send_group_message,
            )
        ]

    async def send_group_message(self, event, text: str = "", group: str = "", song: str = ""):
        if not (text or "").strip() and not (song or "").strip():
            return "没说要发什么"
        if not self._is_admin(str(event.get_sender_id())):
            return "只有管理员能让我跨群发消息"

        c = self.conf()
        mapping = _groups()
        gid = ""
        kw = (group or "").strip()
        if kw:
            for name, g in mapping.items():
                if kw == name or kw in name or kw == g:
                    gid = g
                    break
        if not gid:
            gid = str(c.get("default_group") or "")
        if not gid:
            gid = str(self.kernel.cfg.get("group") or "")
        if not gid and mapping:
            gid = next(iter(mapping.values()))
        if not gid:
            return "不知道发哪个群，也没配置默认群"

        ok, why = self._allowed(gid)
        if not ok:
            return why

        title = ""
        if (song or "").strip():
            try:
                sys.path.insert(0, str(ROOT / "scripts"))
                import netease

                hits = netease.search(song.strip(), 1)
            except Exception as exc:  # noqa: BLE001
                return f"搜歌失败：{exc}"
            if not hits:
                return f"没搜到「{song.strip()}」"
            item = hits[0]
            title = f"{item['name']} - {item['artists']}"
            payload = [{"type": "music", "data": {"type": "163", "id": item["id"]}}]
            if (text or "").strip():
                payload.insert(0, {"type": "text", "data": {"text": text.strip()}})
        else:
            payload = [{"type": "text", "data": {"text": text.strip()}}]

        try:
            r = requests.post(
                ONEBOT + "/send_group_msg",
                headers={"Authorization": f"Bearer {_token()}"},
                json={"group_id": int(gid), "message": payload},
                timeout=20,
            )
            if r.status_code == 200 and (r.json() or {}).get("status") in ("ok", None):
                logger.info(f"[sendto] 已发到群 {gid}：{title or text[:30]}")
                return f"已把《{title}》发到群里（{gid}）" if title else f"已发到群里（{gid}）"
            return f"发送失败：{r.status_code} {r.text[:80]}"
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[sendto] 发送异常: {exc}")
            return f"发送出错：{exc}"
