"""把 personas/大肥鱼.txt 写进 AstrBot 的数据库。

用法（AstrBot 跑过一次、数据库生成之后）：
    D:\\QQBot\\AstrBot\\.venv\\Scripts\\python.exe D:\\QQBot\\scripts\\import_persona.py

改 QQBOT_ROOT 可以换项目根。
"""

import os
import pathlib
import sqlite3

ROOT = pathlib.Path(os.environ.get("QQBOT_ROOT", r"D:\QQBot"))
DB = ROOT / "AstrBot" / "data" / "data_v4.db"
TXT = ROOT / "personas" / "大肥鱼.txt"
NAME = "大肥鱼"


def main() -> int:
    if not DB.exists():
        print(f"数据库不存在：{DB}")
        print("先启动一次 AstrBot，让它生成 data_v4.db。")
        return 1
    if not TXT.exists():
        print(f"人设文件不存在：{TXT}")
        return 1
    text = TXT.read_text(encoding="utf-8").strip()
    con = sqlite3.connect(DB, timeout=20)
    cur = con.cursor()
    row = cur.execute(
        "select id from personas where persona_id = ?", (NAME,)
    ).fetchone()
    if row:
        cur.execute(
            "update personas set system_prompt = ?, updated_at = datetime('now') where id = ?",
            (text, row[0]),
        )
        action = "更新"
    else:
        cur.execute(
            "insert into personas (created_at, updated_at, persona_id, system_prompt, "
            "begin_dialogs, sort_order) values (datetime('now'), datetime('now'), ?, ?, '[]', 0)",
            (NAME, text),
        )
        action = "新建"
    con.commit()
    con.close()
    print(f"{action}人格「{NAME}」，{len(text)} 字")
    print("重启 AstrBot，或在 6185 面板里把默认人格选成它。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
