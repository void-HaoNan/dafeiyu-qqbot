"""把对话历史里的图片换成一句占位，避免 base64 撑爆上下文，也避免坏掉的 image_url。"""

import json
import re
import sqlite3

from . import paths

DB = paths.ROOT / "AstrBot" / "data" / "data_v4.db"
PATTERNS = [
    re.compile(r"data:image/[a-zA-Z]+;base64,[A-Za-z0-9+/=]+"),
    re.compile(r"base64://[A-Za-z0-9+/=]+"),
]
PLACEHOLDER = "[图片]"


def _clean_text(text: str) -> str:
    for p in PATTERNS:
        text = p.sub(PLACEHOLDER, text)
    return text


def _clean_messages(content: str) -> str:
    try:
        messages = json.loads(content)
    except (TypeError, ValueError):
        return _clean_text(content or "")
    if not isinstance(messages, list):
        return _clean_text(content or "")
    for msg in messages:
        parts = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(parts, str):
            msg["content"] = _clean_text(parts)
            continue
        if not isinstance(parts, list):
            continue
        new_parts = []
        for part in parts:
            if not isinstance(part, dict):
                new_parts.append(part)
                continue
            if part.get("type") in ("image_url", "image"):
                new_parts.append({"type": "text", "text": PLACEHOLDER})
                continue
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                part["text"] = _clean_text(part["text"])
            new_parts.append(part)
        msg["content"] = new_parts
    return json.dumps(messages, ensure_ascii=False)


def _update(cid: str, content: str) -> int:
    new = _clean_messages(content)
    if new == content:
        return 0
    con = sqlite3.connect(str(DB), timeout=10)
    con.execute("update conversations set content=? where conversation_id=?", (new, cid))
    con.commit()
    con.close()
    return len(content) - len(new)


def strip_images(umo: str) -> int:
    """把这个会话里的图片换成占位，返回省下的字符数。"""
    try:
        con = sqlite3.connect(str(DB), timeout=10)
        row = con.execute(
            "select conversation_id, content from conversations where user_id=?",
            (umo,),
        ).fetchone()
        con.close()
        if not row:
            return 0
        return _update(row[0], row[1] or "")
    except Exception:  # noqa: BLE001
        return 0


def strip_all() -> tuple[int, int]:
    """全库修一遍，返回 (会话数, 省下的字符数)。"""
    con = sqlite3.connect(str(DB), timeout=15)
    rows = con.execute("select conversation_id, content from conversations").fetchall()
    con.close()
    count = saved = 0
    for cid, content in rows:
        n = _update(cid, content or "")
        if n:
            count += 1
            saved += n
    return count, saved
