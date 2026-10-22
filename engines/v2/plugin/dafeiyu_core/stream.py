import json
import time

from . import paths


def write(event, text: str, direction: str = "out", images: int = 0) -> None:
    if not text and not images:
        return
    rec = {
        "ts": time.time(),
        "dir": direction,
        "conv_id": str(event.get_group_id() or event.get_sender_id() or ""),
        "is_group": bool(event.get_group_id()),
        "sender": "大肥鱼" if direction == "out" else (event.get_sender_name() or ""),
        "uid": str((event.get_self_id() if direction == "out" else event.get_sender_id()) or ""),
        "umo": event.unified_msg_origin,
        "text": text,
        "images": images,
    }
    try:
        with paths.STREAM_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
