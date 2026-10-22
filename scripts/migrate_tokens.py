"""把旧的 token_usage.jsonl 一次性导入 tokens.db。

之后 token 只写 tokens.db，不再读 jsonl。重复跑会检测到已有数据并跳过。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

ROOT = Path(os.environ.get("QQBOT_ROOT", r"D:\QQBot"))
SRC = ROOT / "data" / "dafeiyu" / "token_usage.jsonl"
DB = ROOT / "data" / "dafeiyu" / "tokens.db"
SKIP_UIDS = {"47781819"}  # @全体成员 伪发送者


def main() -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=10)
    try:
        con.execute("pragma journal_mode=wal")
        con.execute(
            "create table if not exists usage ("
            "id integer primary key autoincrement,"
            "ts real not null,"
            "uid text not null default '',"
            "gid text not null default '',"
            "tokens integer not null default 0)"
        )
        con.execute("create index if not exists idx_usage_ts on usage(ts)")
        con.execute("create index if not exists idx_usage_gid on usage(gid)")
        con.execute("create index if not exists idx_usage_uid on usage(uid)")
        count = con.execute("select count(*) from usage").fetchone()[0]
        if count:
            print(f"tokens.db 已有 {count} 条，跳过导入")
            return
        rows = []
        if SRC.exists():
            for line in SRC.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                rows.append((
                    float(rec.get("ts") or time.time()),
                    str(rec.get("uid") or ""),
                    str(rec.get("gid") or ""),
                    int(rec.get("tokens") or 0),
                ))
        rows = [r for r in rows if r[1] not in SKIP_UIDS]
        con.executemany(
            "insert into usage(ts, uid, gid, tokens) values (?, ?, ?, ?)", rows
        )
        con.commit()
        total = sum(r[3] for r in rows)
        print(f"导入 {len(rows)} 条，共 {total} token")
    finally:
        con.close()


if __name__ == "__main__":
    main()
