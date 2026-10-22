import json
import os

import requests

from . import paths


class OneBot:
    """统一 OneBot HTTP 客户端：URL、token 只在这里读。"""

    def __init__(self, url: str | None = None, token: str | None = None) -> None:
        self.url = (url or os.environ.get("ONEBOT_URL") or "http://127.0.0.1:3000").rstrip("/")
        self.token = token if token is not None else self._find_token()

    def _find_token(self) -> str:
        env = os.environ.get("ONEBOT_TOKEN", "").strip()
        if env:
            return env
        for cfg in (paths.ROOT / "SnowLuma" / "config").glob("onebot_*.json"):
            try:
                data = json.loads(cfg.read_text(encoding="utf-8-sig"))
                return data["networks"]["httpServers"][0]["accessToken"]
            except Exception:  # noqa: BLE001
                continue
        return ""

    def call(self, action: str, **params) -> dict:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            resp = requests.post(
                f"{self.url}/{action}", json=params, headers=headers, timeout=20
            )
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "error": str(exc)}
