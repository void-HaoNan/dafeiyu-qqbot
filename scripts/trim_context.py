import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(os.environ.get("QQBOT_ROOT") or Path(__file__).resolve().parents[1])
DB = ROOT / "AstrBot" / "data" / "data_v4.db"
LIMIT = 400_000
KEEP = 120

con = sqlite3.connect(str(DB), timeout=20)
cur = con.cursor()
rows = list(cur.execute("select conversation_id, length(content) from conversations where user_id like '%GroupMessage%' and length(content) > ?", (LIMIT,)))
for cid, size in rows:
    content = cur.execute("select content from conversations where conversation_id=?", (cid,)).fetchone()[0]
    msgs = json.loads(content)
    if len(msgs) <= KEEP:
        print(f"skip {cid[:8]} chars={size} msgs={len(msgs)}")
        continue
    cur.execute("update conversations set content=? where conversation_id=?", (json.dumps(msgs[-KEEP:], ensure_ascii=False), cid))
    print(f"trimmed {cid[:8]}: {size} chars, {len(msgs)} -> {KEEP} msgs")
con.commit()
con.close()
