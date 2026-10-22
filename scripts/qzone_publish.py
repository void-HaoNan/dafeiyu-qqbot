# -*- coding: utf-8 -*-
"""发一条 QQ 空间说说。给插件和控制台调用：python qzone_publish.py --text "..." """

import argparse
import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(os.environ.get("QQBOT_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "AstrBot"))
sys.path.insert(0, str(ROOT / "AstrBot" / "data" / "plugins"))

_snow_cfg = next(
    iter(sorted((ROOT / "SnowLuma" / "config").glob("onebot_*.json"))),
    ROOT / "SnowLuma" / "config" / "onebot.json",
)
SNOW = json.load(open(_snow_cfg, encoding="utf-8-sig"))
TOKEN = SNOW["networks"]["httpServers"][0]["accessToken"]


def onebot(action, payload):
    req = urllib.request.Request(
        "http://127.0.0.1:3000/" + action,
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


class FakeClient:
    async def get_cookies(self, domain="user.qzone.qq.com"):
        return onebot("get_cookies", {"domain": domain}).get("data") or {}

    async def get_login_info(self):
        return onebot("get_login_info").get("data") or {}


async def publish(text: str) -> dict:
    from astrbot_plugin_qzone.core.model import Post
    from astrbot_plugin_qzone.core.qzone.api import QzoneAPI
    from astrbot_plugin_qzone.core.qzone.session import QzoneSession

    cfg = SimpleNamespace(client=FakeClient(), timeout=20, cookie_ttl=600)
    session = QzoneSession(cfg)
    api = QzoneAPI(session, cfg)
    try:
        resp = await api.publish(Post(content=text, text=text))
        tid = (resp.data or {}).get("tid") or (resp.raw or {}).get("tid")
        return {"ok": bool(resp.ok) and bool(tid), "tid": tid, "code": resp.code, "message": resp.message}
    finally:
        await api.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True)
    args = ap.parse_args()
    try:
        out = asyncio.run(publish(args.text))
    except Exception as exc:  # noqa: BLE001
        out = {"ok": False, "error": str(exc)}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
