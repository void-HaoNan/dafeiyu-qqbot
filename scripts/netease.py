#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""网易云账号接口（本地会话，不存密码）。

登录态来自浏览器 cookie（MUSIC_U 等），存在 data/netease_session.json，
不会进版本库，也不会打到日志里。

用法（AstrBot 的 venv 跑）：
  python scripts/netease.py account
  python scripts/netease.py playlists
  python scripts/netease.py search 起风了
  python scripts/netease.py url 1330348068
  python scripts/netease.py add 123456 1330348068
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("QQBOT_ROOT") or Path(__file__).resolve().parents[1])
SESSION = ROOT / "data" / "netease_session.json"
COLLECTED = ROOT / "data" / "music_collected.jsonl"
BASE = "https://music.163.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def load_session() -> dict:
    try:
        return json.loads(SESSION.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_session(data: dict) -> None:
    SESSION.parent.mkdir(parents=True, exist_ok=True)
    SESSION.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def parse_cookie(raw: str) -> dict:
    out = {}
    for part in (raw or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def cookie_header() -> str:
    s = load_session()
    return s.get("cookie", "")


def csrf() -> str:
    return parse_cookie(cookie_header()).get("__csrf", "")


def request(path: str, data: dict | None = None, timeout: int = 20) -> dict:
    url = BASE + path
    headers = {
        "User-Agent": UA,
        "Referer": "https://music.163.com/",
        "Cookie": cookie_header(),
    }
    if data is not None:
        if csrf():
            data = {**data, "csrf_token": csrf()}
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    else:
        req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def account() -> dict:
    if not cookie_header():
        return {"ok": False, "error": "还没设置 cookie"}
    try:
        d = request("/api/nuser/account/get")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    prof = d.get("profile") or {}
    if not prof.get("userId"):
        return {"ok": False, "error": "cookie 失效了，重新复制一份"}
    return {"ok": True, "uid": prof["userId"], "nickname": prof.get("nickname"), "vip": prof.get("vipType", 0)}


def playlists(uid: int, own_only: bool = True) -> list[dict]:
    """自己的歌单。默认过滤掉收藏的别人的歌单（creator 不是自己的）。"""
    d = request(f"/api/user/playlist?uid={uid}&limit=100&offset=0")
    out = []
    for p in d.get("playlist") or []:
        creator = (p.get("creator") or {}).get("userId") or p.get("userId")
        if own_only and creator and int(creator) != int(uid):
            continue
        out.append({"id": p.get("id"), "name": p.get("name"), "count": p.get("trackCount")})
    return out


def search(keyword: str, limit: int = 5) -> list[dict]:
    q = urllib.parse.quote(keyword)
    d = request(f"/api/search/get/web?s={q}&type=1&limit={limit}")
    songs = (d.get("result") or {}).get("songs") or []
    return [
        {"id": s.get("id"), "name": s.get("name"), "artists": "、".join(a.get("name", "") for a in s.get("artists") or [])}
        for s in songs
    ]


def song_url(song_id: int) -> str:
    d = request(f"/api/song/enhance/player/url?ids=%5B{song_id}%5D&br=320000")
    items = d.get("data") or []
    return (items[0].get("url") if items else "") or ""


def add_tracks(pid: int, ids: list[int]) -> dict:
    d = request(
        "/api/playlist/manipulate/tracks",
        {"op": "add", "pid": pid, "trackIds": json.dumps(ids), "imme": "true"},
    )
    return {"ok": d.get("status") == 200 or d.get("code") == 200, "resp": d}


def qr_start() -> dict:
    """扫码登录：拿一个 unikey，二维码内容就是 login?codekey=..."""
    d = request("/api/login/qrcode/unikey", {"type": 1})
    key = d.get("unikey")
    if not key:
        return {"ok": False, "error": d.get("message") or "拿不到 unikey"}
    return {"ok": True, "unikey": key, "url": f"https://music.163.com/login?codekey={key}"}


def qr_poll(unikey: str) -> dict:
    """轮询扫码状态；803 表示成功，顺手把 cookie 存下来。"""
    import http.cookiejar

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(
        f"{BASE}/api/login/qrcode/client/login?key={unikey}&type=1",
        headers={"User-Agent": UA, "Referer": "https://music.163.com/"},
    )
    with opener.open(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8", "ignore"))
    code = d.get("code")
    if code == 803:
        cookies = {c.name: c.value for c in jar}
        print(f"[netease] qr803 cookies={sorted(cookies)}", flush=True)
        header = "; ".join(f"{k}={v}" for k, v in cookies.items())
        if "MUSIC_U" in cookies:
            save_session({"cookie": header, "updated": time.time()})
            return {"code": 803, "ok": True}
        return {"code": 803, "ok": False, "error": "登录成功但没拿到 MUSIC_U"}
    return {"code": code, "msg": d.get("message")}


def log_collected(rec: dict) -> None:
    COLLECTED.parent.mkdir(parents=True, exist_ok=True)
    with COLLECTED.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("account")
    sub.add_parser("playlists")
    p1 = sub.add_parser("search"); p1.add_argument("keyword")
    p2 = sub.add_parser("url"); p2.add_argument("id", type=int)
    p3 = sub.add_parser("add"); p3.add_argument("pid", type=int); p3.add_argument("ids", nargs="+", type=int)
    p4 = sub.add_parser("set-cookie"); p4.add_argument("cookie")
    args = ap.parse_args()

    if args.cmd == "set-cookie":
        save_session({"cookie": args.cookie, "updated": time.time()})
        print(json.dumps(account(), ensure_ascii=False))
        return 0
    if args.cmd == "account":
        print(json.dumps(account(), ensure_ascii=False))
    elif args.cmd == "playlists":
        a = account()
        print(json.dumps(playlists(a["uid"]) if a.get("ok") else a, ensure_ascii=False))
    elif args.cmd == "search":
        print(json.dumps(search(args.keyword), ensure_ascii=False))
    elif args.cmd == "url":
        print(json.dumps({"url": song_url(args.id)}, ensure_ascii=False))
    elif args.cmd == "add":
        print(json.dumps(add_tracks(args.pid, args.ids), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())