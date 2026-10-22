"""人物档案：记住谁是谁，每个聊过天的人一份。

- 收消息时自动记 QQ、昵称、最后活跃、消息数、字数、在哪些群出现
- 每次请求把「当前说话人」和「本群最近说话的人（带 QQ）」交给模型
- 模型可以用 remember_person 工具自己补充称呼、关系、备注
- 控制台读同一份 data/dafeiyu/profiles.json
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from astrbot.api import logger

from dafeiyu_core.module import Module, ToolSpec, register

try:
    from dafeiyu_core.paths import ROOT
except Exception:  # noqa: BLE001
    ROOT = Path(os.environ.get("QQBOT_ROOT", r"D:\QQBot"))

STORE = ROOT / "data" / "dafeiyu" / "profiles.json"
TOKEN_DB = ROOT / "data" / "dafeiyu" / "tokens.db"
MAINT_BUFFER = ROOT / "data" / "dafeiyu" / "maintenance_buffer.json"
RELATIONS = ("生人", "熟人", "主人")
MAX_NOTES = 1500
MAX_GROUPS = 50
_LOCK = threading.Lock()

# QQ 的 @全体成员 伪发送者；不能当人记进档案
SYSTEM_UIDS = {"47781819"}
SYSTEM_NAMES = {"@全体成员", "全体成员"}


def _is_system_sender(uid: str, nick: str = "") -> bool:
    return str(uid or "").strip() in SYSTEM_UIDS or (nick or "").strip() in SYSTEM_NAMES


def _record_token(uid: str, gid: str, total: int) -> None:
    """唯一 token 账本：一条回复一行，私聊 gid 为空，群聊带群号。"""
    try:
        TOKEN_DB.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(TOKEN_DB, timeout=5)
        try:
            con.execute("pragma journal_mode=wal")
            con.execute("pragma busy_timeout=5000")
            con.execute(
                "create table if not exists usage ("
                "id integer primary key autoincrement,"
                "ts real not null,"
                "uid text not null default '',"
                "gid text not null default '',"
                "tokens integer not null default 0)"
            )
            con.execute(
                "create index if not exists idx_usage_ts on usage(ts)"
            )
            con.execute(
                "create index if not exists idx_usage_gid on usage(gid)"
            )
            con.execute(
                "create index if not exists idx_usage_uid on usage(uid)"
            )
            con.execute(
                "insert into usage(ts, uid, gid, tokens) values (?, ?, ?, ?)",
                (time.time(), str(uid), str(gid or ""), int(total)),
            )
            con.commit()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[profile] token 账本写入失败: {exc}")


def load_data() -> dict:
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    users = data.get("users")
    if not isinstance(users, dict):
        data["users"] = {}
    return data


def save_data(data: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_name(f"profiles.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STORE)


def _pop_maintenance(key: str) -> list[dict]:
    """取出维护期间这个群/这个人的消息，取完就清。"""
    try:
        data = json.loads(MAINT_BUFFER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or key not in data:
        return []
    rows = data.pop(key) or []
    try:
        MAINT_BUFFER.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return rows if isinstance(rows, list) else []


@contextlib.contextmanager
def _file_lock():
    """面板和插件是两个进程，用文件锁避免同时改档案互相覆盖。"""
    try:
        import msvcrt
    except ImportError:  # 非 Windows
        yield
        return
    lock = STORE.with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock, "a+b")
    try:
        f.seek(0, os.SEEK_END)
        if f.tell() == 0:
            f.write(b"0")
            f.flush()
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        f.close()


@contextlib.contextmanager
def _locked():
    with _LOCK:
        with _file_lock():
            yield


def _blank(qq: str, name: str, relation: str = "生人") -> dict:
    now = int(time.time())
    return {
        "qq": qq,
        "name": name or qq,
        "alias": "",
        "relation": relation,
        "likes": "",
        "notes": "",
        "first_seen": now,
        "last_seen": now,
        "msg_count": 0,
        "chars": 0,
        "tokens": 0,
        "groups": [],
    }


@register
class ProfileModule(Module):
    name = "profile"
    description = "人物档案 + 统一上下文：当前说话人、群名单、维护补记"
    priority = 20

    async def setup(self) -> None:
        self._admin = ""
        try:
            self._admin = str(self.kernel.cfg.get("admin_qq") or "")
        except Exception:  # noqa: BLE001
            pass
        self._backfill()
        self._heal_names()
        logger.info("[profile] 人物档案已加载")

    def _heal_names(self) -> None:
        """昵称被通知事件写成一串 QQ 号的，按群成员/好友资料补回来。"""
        with _locked():
            data = load_data()
            if not data["users"]:
                return
            names: dict[str, str] = {}
            groups = {str(g) for u in data["users"].values() for g in (u.get("groups") or [])}
            for gid in groups:
                try:
                    res = self.kernel.bot.call("get_group_member_list", group_id=int(gid))
                except Exception:  # noqa: BLE001
                    continue
                for m in (res.get("data") or []):
                    qq = str(m.get("user_id") or "")
                    nick = str(m.get("card") or m.get("nickname") or "").strip()
                    if qq and nick:
                        names.setdefault(qq, nick)
            try:
                res = self.kernel.bot.call("get_friend_list")
                for f in (res.get("data") or []):
                    qq = str(f.get("user_id") or "")
                    nick = str(f.get("remark") or f.get("nickname") or "").strip()
                    if qq and nick:
                        names.setdefault(qq, nick)
            except Exception:  # noqa: BLE001
                pass
            changed = 0
            for qq, user in data["users"].items():
                cur = str(user.get("name") or "").strip()
                if (not cur or cur.isdigit()) and qq in names:
                    user["name"] = names[qq]
                    changed += 1
            if changed:
                save_data(data)
                logger.info(f"[profile] 补回 {changed} 个人的昵称")

    def _backfill(self) -> None:
        """第一次启用时，从消息流里把聊过天的人补出来。"""
        stream = ROOT / "logs" / "panel_stream.jsonl"
        if not stream.exists():
            return
        with _locked():
            data = load_data()
            if data["users"]:
                return
            try:
                lines = stream.read_text(encoding="utf-8", errors="replace").splitlines()[-5000:]
            except OSError:
                return
            for line in lines:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("dir") != "in":
                    continue
                uid = str(rec.get("uid") or "").strip()
                if not uid:
                    continue
                if _is_system_sender(uid, str(rec.get("sender") or "")):
                    continue
                is_new = uid not in data["users"]
                user = self._ensure(data, uid, str(rec.get("sender") or ""))
                ts = int(float(rec.get("ts") or 0))
                if is_new and ts:
                    user["first_seen"] = ts
                    user["last_seen"] = ts
                elif ts:
                    user["last_seen"] = max(int(user.get("last_seen") or 0), ts)
                user["msg_count"] = int(user.get("msg_count") or 0) + 1
                user["chars"] = int(user.get("chars") or 0) + len(str(rec.get("text") or ""))
                if rec.get("is_group") and rec.get("conv_id"):
                    gid = str(rec["conv_id"])
                    groups = [str(g) for g in (user.get("groups") or []) if str(g) != gid]
                    groups.append(gid)
                    user["groups"] = groups[-MAX_GROUPS:]
            save_data(data)
            logger.info(f"[profile] 从消息流补了 {len(data['users'])} 个人")

    def _ensure(self, data: dict, qq: str, nick: str) -> dict:
        user = data["users"].get(qq)
        if not isinstance(user, dict):
            user = _blank(qq, nick)
            data["users"][qq] = user
        if nick:
            user["name"] = nick
        if qq == getattr(self, "_admin", ""):
            user["relation"] = "主人"
        user.setdefault("alias", "")
        user.setdefault("likes", "")
        if user.get("relation") not in RELATIONS:
            user["relation"] = "生人"
        return user

    async def on_message(self, event) -> None:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None) or {}
        if isinstance(raw, dict) and raw.get("post_type") not in (None, "message"):
            return
        uid = str(event.get_sender_id() or "").strip()
        if not uid:
            return
        gid = str(event.get_group_id() or "").strip()
        nick = (event.get_sender_name() or "").strip()
        if _is_system_sender(uid, nick):
            return
        text = event.get_message_str() or ""
        with _locked():
            data = load_data()
            user = self._ensure(data, uid, nick)
            user["last_seen"] = int(time.time())
            user["msg_count"] = int(user.get("msg_count") or 0) + 1
            user["chars"] = int(user.get("chars") or 0) + len(text)
            if gid:
                groups = [str(g) for g in (user.get("groups") or []) if str(g) != gid]
                groups.append(gid)
                user["groups"] = groups[-MAX_GROUPS:]
            save_data(data)

    async def on_llm_response(self, event, response) -> None:
        usage = getattr(response, "usage", None)
        try:
            total = int(getattr(usage, "total", 0) or 0)
        except Exception:  # noqa: BLE001
            total = 0
        if total <= 0:
            return
        uid = str(event.get_sender_id() or "").strip()
        if not uid:
            return
        if _is_system_sender(uid, event.get_sender_name() or ""):
            return
        gid = str(event.get_group_id() or "")
        _record_token(uid, gid, total)
        with _locked():
            data = load_data()
            gid = str(event.get_group_id() or "")
            user = self._ensure(data, uid, (event.get_sender_name() or ""))
            if gid:
                groups = [str(g) for g in (user.get("groups") or []) if str(g) != gid]
                groups.append(gid)
                user["groups"] = groups[-MAX_GROUPS:]
            save_data(data)

    async def on_llm_request(self, event, req) -> None:
        if event.get_extra("_dafeiyu_profile_injected"):
            return
        uid = str(event.get_sender_id() or "").strip()
        if not uid:
            return
        if _is_system_sender(uid, event.get_sender_name() or ""):
            return
        gid = str(event.get_group_id() or "").strip()
        with _locked():
            data = load_data()
            user = data["users"].get(uid)
            if not isinstance(user, dict):
                user = self._ensure(data, uid, (event.get_sender_name() or ""))
                save_data(data)
        who = user.get("alias") or user.get("name") or uid
        lines = [f"【当前说话人】{who}（QQ {uid}，{'群聊' if gid else '私聊'}）"]
        if user.get("relation"):
            lines.append(f"关系：{user['relation']}")
        if user.get("likes"):
            lines.append(f"喜好：{user['likes']}")
        if user.get("notes"):
            lines.append(f"你记得：{user['notes']}")
        lines.append("对方提到名字、喜好、近况时，用 remember_person 记到档案（likes / notes）。")
        if gid:
            recent = self._recent_speakers(data, gid, exclude=uid)
            if recent:
                text = "、".join(f"{name}({qq})" for _, qq, name in recent)
                lines.append(f"本群最近说话的人：{text}")
                lines.append("要禁言、踢人、点名谁，先用上面的昵称和 QQ 对号，别猜。")
        mrows = _pop_maintenance(gid or f"u{uid}")
        if mrows:
            lines.append("维护期间这里有人说过话，现在你回来了，可以自然接上：")
            for r in mrows[-30:]:
                lines.append(f"{r.get('name') or '某人'}：{r.get('text') or ''}")
        block = "\n".join(lines)
        try:
            from astrbot.core.agent.message import TextPart

            part = TextPart(text=block)
            part._no_save = True
            req.extra_user_content_parts.append(part)
            event.set_extra("_dafeiyu_profile_injected", True)
            logger.info(f"[profile] 注入说话人 {who}({uid}) 群={gid or '-'} 备注={'有' if user.get('notes') else '无'}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[profile] 注入说话人失败: {exc}")

    @staticmethod
    def _recent_speakers(data: dict, gid: str, exclude: str, limit: int = 10) -> list[tuple]:
        now = time.time()
        items = []
        for qq, user in (data.get("users") or {}).items():
            if qq == exclude or not isinstance(user, dict):
                continue
            if gid not in [str(g) for g in (user.get("groups") or [])]:
                continue
            seen = int(user.get("last_seen") or 0)
            if now - seen > 7 * 86400:
                continue
            name = user.get("alias") or user.get("name") or qq
            items.append((seen, qq, name))
        items.sort(key=lambda x: x[0], reverse=True)
        return items[:limit]

    def tools(self):
        return [
            ToolSpec(
                "remember_person",
                "记下当前和你说话的人的信息，之后聊天会自动带上。"
                "对方报了名字、说了喜好、关系变熟或变生分时调用。"
                "只记当前说话人，不要拿别人的信息乱填。",
                {
                    "type": "object",
                    "properties": {
                        "alias": {
                            "type": "string",
                            "description": "希望怎么称呼这个人，比如「阿南」「石明子」；不改就留空",
                        },
                        "relation": {
                            "type": "string",
                            "description": "关系：生人 / 熟人 / 主人；不改就留空",
                        },
                        "likes": {
                            "type": "string",
                            "description": "这个人的喜好，比如「日系摇滚、白饭、猫」；知道就填，不知道留空",
                        },
                        "notes": {
                            "type": "string",
                            "description": "关于这个人的新信息，一句话，比如「在准备考研」",
                        },
                    },
                },
                self.remember_person,
            )
        ]

    async def remember_person(self, event, alias: str = "", relation: str = "", likes: str = "", notes: str = ""):
        uid = str(event.get_sender_id() or "").strip()
        if not uid:
            return "不知道现在是谁在说话，没法记"
        if _is_system_sender(uid, event.get_sender_name() or ""):
            return "这是系统消息，不记进档案"
        alias = (alias or "").strip()[:40]
        relation = (relation or "").strip()
        likes = (likes or "").strip()[:120]
        notes = (notes or "").strip()
        with _locked():
            data = load_data()
            user = self._ensure(data, uid, (event.get_sender_name() or ""))
            if alias:
                user["alias"] = alias
            if relation in RELATIONS and not (relation == "主人" and uid != self._admin):
                user["relation"] = relation
            if likes:
                user["likes"] = likes
            if notes:
                old = (user.get("notes") or "").strip()
                if notes not in old:
                    merged = f"{old}；{notes}" if old else notes
                    user["notes"] = merged[-MAX_NOTES:]
            save_data(data)
        return f"记住了：{user.get('alias') or user.get('name')}（{user.get('relation')}）"
