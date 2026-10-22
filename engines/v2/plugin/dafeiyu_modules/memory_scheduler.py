"""把群聊上下文定时整理进个人档案。

触发有两种：一个群攒够 25 条新消息先整理一次；另外每 30 分钟兜底扫一遍，
免得冷群一直不更新。整理用的是短 JSON 请求，只写有把握的喜好和印象。
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import time
import uuid

from astrbot.api import logger

from dafeiyu_core.module import Module, register
from dafeiyu_modules.profile import MAX_NOTES, _locked, load_data, save_data

try:
    from dafeiyu_core.paths import ROOT
except Exception:  # noqa: BLE001
    ROOT = pathlib.Path(os.environ.get("QQBOT_ROOT", r"D:\QQBot"))

STREAM = ROOT / "logs" / "panel_stream.jsonl"
STATE = ROOT / "data" / "dafeiyu" / "summary_state.json"
REQUESTS = ROOT / "data" / "dafeiyu" / "summary_requests.json"
THRESHOLD = 25
INTERVAL = 1800


def _load_state() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(data: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_name(f"summary_state.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, STATE)
    except OSError:
        pass


def _take_requests() -> list[dict]:
    try:
        data = json.loads(REQUESTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    try:
        REQUESTS.write_text("[]", encoding="utf-8")
    except OSError:
        pass
    return [x for x in data if isinstance(x, dict)]


def _messages(gid: str, limit: int = 300) -> list[dict]:
    try:
        lines = STREAM.read_text(encoding="utf-8", errors="replace").splitlines()[-1500:]
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if str(rec.get("conv_id") or "") != gid or not rec.get("is_group"):
            continue
        text = (rec.get("text") or "").strip()
        if not text or text == "[图片]":
            continue
        rows.append(rec)
    return rows[-limit:]


def _user_messages(qq: str, limit: int = 300) -> list[dict]:
    try:
        lines = STREAM.read_text(encoding="utf-8", errors="replace").splitlines()[-3000:]
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if str(rec.get("uid") or "") != qq or rec.get("dir") == "out":
            continue
        text = (rec.get("text") or "").strip()
        if not text or text == "[图片]":
            continue
        rows.append(rec)
    return rows[-limit:]


@register
class MemoryScheduler(Module):
    name = "memory_scheduler"
    description = "按消息量或定时把群聊整理进个人档案"
    priority = 95

    def __init__(self, kernel):
        super().__init__(kernel)
        self._task = None
        self._locks: dict[str, asyncio.Lock] = {}

    async def setup(self) -> None:
        self._task = asyncio.create_task(self._loop())
        asyncio.create_task(self._initial())
        asyncio.create_task(self._watch_requests())
        logger.info("[memory_scheduler] 自动整理已启动")

    async def _watch_requests(self) -> None:
        while True:
            await asyncio.sleep(5)
            for req in _take_requests():
                gid = str(req.get("gid") or "").strip()
                qq = str(req.get("qq") or "").strip() or None
                if gid and not gid.isdigit():
                    continue
                if not gid and not qq:
                    continue
                asyncio.create_task(self._summarize(gid or "", only_qq=qq))

    async def _initial(self) -> None:
        await asyncio.sleep(10)
        state = _load_state()
        if state.get("bootstrapped"):
            return
        groups = set()
        try:
            for line in STREAM.read_text(encoding="utf-8", errors="replace").splitlines()[-1500:]:
                rec = json.loads(line)
                if rec.get("is_group") and rec.get("conv_id"):
                    groups.add(str(rec["conv_id"]))
        except (OSError, ValueError):
            pass
        for gid in groups:
            await self._summarize(gid)
        state = _load_state()
        state["bootstrapped"] = True
        _save_state(state)

    async def on_message(self, event) -> None:
        gid = str(event.get_group_id() or "")
        if not gid:
            return
        if str(event.get_sender_id() or "") == str(event.get_self_id() or ""):
            return
        text = (event.get_message_str() or "").strip()
        if len(text) < 2:
            return
        state = _load_state()
        groups = state.setdefault("groups", {})
        item = groups.setdefault(gid, {"count": 0, "last": 0})
        item["count"] = int(item.get("count") or 0) + 1
        _save_state(state)
        if item["count"] >= THRESHOLD:
            asyncio.create_task(self._summarize(gid))

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(INTERVAL)
            try:
                for gid in list((_load_state().get("groups") or {}).keys()):
                    if int((_load_state().get("groups", {}).get(gid, {}).get("count") or 0)) > 0:
                        await self._summarize(gid)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[memory_scheduler] 定时整理失败: {exc}")

    async def _summarize(self, gid: str, only_qq: str | None = None) -> None:
        logger.info(f"[memory_scheduler] 开始整理 gid={gid or '-'} qq={only_qq or '-'}")
        lock = self._locks.setdefault(gid or f"u{only_qq}", asyncio.Lock())
        if lock.locked():
            logger.info("[memory_scheduler] 上一个整理还没结束，跳过")
            return
        async with lock:
            rows = _messages(gid) if gid else _user_messages(only_qq or "")
            if len(rows) < 3:
                logger.info(f"[memory_scheduler] 消息太少 {len(rows)} 条")
                return
            speakers = {}
            all_speakers = {}
            for rec in rows:
                if rec.get("dir") == "out":
                    continue
                uid = str(rec.get("uid") or "")
                if uid:
                    all_speakers[uid] = rec.get("sender") or uid
            if only_qq:
                if only_qq in all_speakers:
                    speakers[only_qq] = all_speakers[only_qq]
            else:
                speakers = all_speakers
            if not speakers:
                return
            lines = []
            for rec in rows:
                who = rec.get("sender") or "群友"
                uid = rec.get("uid") or ""
                lines.append(f"{who}({uid})：{(rec.get('text') or '')[:200]}")
            with _locked():
                old_profiles = load_data().get("users") or {}
            old_lines = []
            for qq, name in speakers.items():
                old = old_profiles.get(qq) or {}
                old_lines.append(
                    f"{name}({qq}) 旧档案：喜好={old.get('likes') or '无'}；印象={old.get('notes') or '无'}"
                )
            prompt = (
                "下面是这个人的最近聊天，以及这些人已有的旧档案。\n"
                "请把旧档案和新聊天合并成完整档案，旧档案里的事实不要丢。\n"
                "输出 JSON，格式：{\"users\":[{\"qq\":\"QQ号\",\"likes\":\"喜好\",\"notes\":\"印象\"}]}\n"
                "likes 写 3 到 5 个具体喜好；notes 必须写 4 到 6 句，总长度至少 120 字、最多 400 字，"
                "用分号隔开，分别写性格和说话习惯、最近在做什么、和本鱼的关系、明确提过的喜好或经历。\n"
                "信息不够就只写能确认的，别硬编，但不要只写一句话。只写聊天里明确出现过的内容，不要复述原句。\n\n"
                "旧档案：\n" + "\n".join(old_lines) + "\n\n"
                "最近聊天：\n" + "\n".join(lines[-200:])
            )
            try:
                provider = await self.kernel.context.get_using_provider_async()
                if not provider:
                    return
                resp = await provider.text_chat(
                    prompt=prompt,
                    session_id=uuid.uuid4().hex,
                    system_prompt="你是信息整理器，只输出合法 JSON，不要解释。",
                    persist=False,
                )
                text = (getattr(resp, "completion_text", "") or "").strip()
                text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                data = json.loads(text)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[memory_scheduler] 整理 {gid} 失败: {exc}")
                return
            items = data.get("users") if isinstance(data, dict) else []
            if not isinstance(items, list):
                logger.warning(f"[memory_scheduler] 返回格式不对: {str(data)[:200]}")
                return
            logger.info(f"[memory_scheduler] 整理结果 {len(items)} 人")
            with _locked():
                profiles = load_data()
                changed = False
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    qq = str(it.get("qq") or "").strip()
                    if qq not in speakers:
                        continue
                    if only_qq and qq != only_qq:
                        continue
                    user = self._ensure(profiles, qq, speakers[qq])
                    likes = self._text(it.get("likes"))[:200]
                    notes = self._text(it.get("notes"))[:1200]
                    if likes:
                        user["likes"] = likes
                        changed = True
                    if notes:
                        user["notes"] = notes[:MAX_NOTES]
                        changed = True
                if changed:
                    save_data(profiles)
            if only_qq:
                return
            state = _load_state()
            g = state.setdefault("groups", {}).setdefault(gid, {})
            g["count"] = 0
            g["last"] = int(time.time())
            _save_state(state)

    def _text(self, value) -> str:
        if isinstance(value, list):
            parts = [self._text(v) for v in value]
            return "、".join(p for p in parts if p).strip()
        return str(value or "").strip()

    def _ensure(self, data: dict, qq: str, name: str) -> dict:
        users = data.setdefault("users", {})
        user = users.get(qq)
        if not isinstance(user, dict):
            user = {
                "qq": qq, "name": name or qq, "alias": "", "relation": "生人",
                "likes": "", "notes": "", "first_seen": int(time.time()),
                "last_seen": int(time.time()), "msg_count": 0, "chars": 0,
                "tokens": 0, "groups": [],
            }
            users[qq] = user
        return user
