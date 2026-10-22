"""AI 说说：隔一阵看最近聊天，由模型自己决定发不发，每天最多一条。

流程：
  每 2 分钟醒一次 → 到点（默认 90 分钟）或控制台点了「立即判断」→
  读最近聊天 → 让模型输出 JSON（发不发、发什么）→
  草稿模式存起来等控制台确认，自动模式直接调 qzone_publish.py 发出去。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from astrbot.api import logger
from dafeiyu_core.module import Module, register

try:
    ROOT = Path(os.environ.get("QQBOT_ROOT", str(Path(__file__).resolve().parents[4])))
except IndexError:  # 仓库里直接跑时没有 5 层父目录
    ROOT = Path(os.environ.get("QQBOT_ROOT", str(Path(__file__).resolve().parents[2])))
STATE = ROOT / "data" / "qzone_ai_state.json"
DRAFTS = ROOT / "data" / "qzone_drafts.json"
STREAM = ROOT / "logs" / "panel_stream.jsonl"
PUBLISH = ROOT / "scripts" / "qzone_publish.py"
PY = str(ROOT / "AstrBot" / ".venv" / "Scripts" / "python.exe")

DEFAULT = {"enabled": True, "mode": "both", "interval_min": 90, "last_check": 0, "last_post_date": "", "force": False, "diary_enabled": True, "diary_time": "20:00", "last_diary_date": "", "force_diary": False}

PROMPT = """你是「大肥鱼」，一只有点傲娇、爱吃饭、会犯困的赛博蓝鲸。下面是你今天看到的群聊和私聊片段。

{runtime}

请你像一个真人刷完手机那样判断：现在想不想发一条 QQ 空间说说。
- 想发：说一件事、一个念头、一句吐槽，20 到 60 字，口语，可以带一点情绪和状态（困、饿、烦、开心）。
- 这条说说要给所有人看，不是只给群里的人看：不要用只有群里才懂的梗、缩写、人名代号，
  提到一件事就用半句话交代清楚是什么事，让不认识的人也能看懂你在说什么。
- 不想发就明确说不想，不要硬凑。

只输出一个 JSON，不要解释，不要代码块：
{{"post": true, "review": false, "reason": "一句话说明你为什么现在想发", "text": "说说正文"}}
或
{{"post": false, "review": false, "reason": "", "text": ""}}

review 为 true 表示这条你自己也拿不准（涉及具体的人、比较敏感、怕说错），想先给主人看一眼；
普通吐槽、状态、感慨就写 false。"""


DIARY_PROMPT = """你是「大肥鱼」，一只有点傲娇、爱吃饭、会犯困的赛博蓝鲸。今天快过完了，下面是今天群里和你聊过的话。

请你写一篇今天的日记，要求：
- 150 到 300 字，第一人称，口语，像真人在手机备忘录里随手写的
- 可以写今天印象最深的一两件事、自己的心情、困不困饿不饿
- 写给所有人看：不要用只有群里才懂的梗、缩写、代号，提到谁就用一句话说清是谁
- 不要写「今天」开头，不要列清单，不要总结得像工作汇报

只输出一个 JSON，不要解释，不要代码块：
{{"post": true, "text": "日记正文"}}"""


def load(path: Path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(default, dict):
            for k, v in default.items():
                data.setdefault(k, v)
        return data
    except (OSError, ValueError):
        return default.copy() if isinstance(default, dict) else default


def save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def today_chat(limit: int = 200) -> str:
    """只取今天 0 点之后的聊天，给日记用。"""
    try:
        lines = STREAM.read_text(encoding="utf-8").splitlines()[-400:]
    except OSError:
        return ""
    start = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
    out = []
    for line in reversed(lines):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if float(r.get("ts") or 0) < start:
            break
        txt = (r.get("text") or "").strip()
        if not txt:
            continue
        who = "大肥鱼" if r.get("dir") == "out" else (r.get("sender") or "群友")
        out.append(f"{who}：{txt}")
        if len(out) >= limit:
            break
    return "\n".join(reversed(out))


def recent_chat(limit: int = 60) -> str:
    try:
        lines = STREAM.read_text(encoding="utf-8").splitlines()[-200:]
    except OSError:
        return ""
    out = []
    now = time.time()
    for line in reversed(lines):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if now - float(r.get("ts") or 0) > 6 * 3600:
            break
        txt = (r.get("text") or "").strip()
        if not txt:
            continue
        who = "大肥鱼" if r.get("dir") == "out" else (r.get("sender") or "群友")
        out.append(f"{who}：{txt}")
        if len(out) >= limit:
            break
    return "\n".join(reversed(out))


class QzoneEngine:
    def __init__(self) -> None:
        self.context = None
        self._task: asyncio.Task | None = None
        self._busy = False

    async def initialize(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("[qzone_ai] 已启动")

    async def terminate(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(10)
                await self._tick()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[qzone_ai] 循环出错: {exc}")

    async def _tick(self) -> None:
        if self._busy:
            return
        st = load(STATE, DEFAULT)
        today = time.strftime("%Y-%m-%d")
        if st.get("force_diary"):
            self._busy = True
            try:
                await self._diary(st)
            finally:
                self._busy = False
            return
        if st.get("diary_enabled", True) and st.get("last_diary_date") != today:
            if time.strftime("%H:%M") >= str(st.get("diary_time") or "20:00"):
                self._busy = True
                try:
                    await self._diary(st)
                finally:
                    self._busy = False
                return
        if not st.get("enabled", True):
            return
        force = bool(st.get("force"))
        due = time.time() - float(st.get("last_check") or 0) >= float(st.get("interval_min") or 90) * 60
        if not force and not due:
            return
        self._busy = True
        try:
            await self._decide(st)
        finally:
            self._busy = False

    async def _decide(self, st: dict) -> None:
        chat = recent_chat()
        if len(chat.splitlines()) < 8:
            st["last_check"] = time.time()
            st["force"] = False
            st["last_result"] = time.strftime("%m-%d %H:%M") + " 聊天太少，跳过"
            save(STATE, st)
            return

        provider = self.context.get_using_provider()
        if not provider:
            logger.warning("[qzone_ai] 没有可用的对话模型")
            return
        forced = bool(st.get("force"))
        runtime = f"现在是 {time.strftime('%H:%M')}，" + ("管理员手动要求现在发一条，即使今天已经发过也可以再发。" if forced else ("今天已经发过一条了，别再发。" if st.get("last_post_date") == time.strftime("%Y-%m-%d") else "今天还没发过。"))
        try:
            resp = await provider.text_chat(
                prompt=PROMPT.format(runtime=runtime) + "\n\n最近的聊天：\n" + chat,
                system_prompt="你是大肥鱼，说话短、口语、有点傲娇。现在只做一件事：决定发不发空间说说，并按要求的 JSON 输出。",
            )
            raw = (resp.completion_text or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[qzone_ai] 模型调用失败: {exc}")
            st["last_check"] = time.time()
            st["force"] = False
            save(STATE, st)
            return

        st["last_check"] = time.time()
        st["force"] = False
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            data = json.loads(m.group(0)) if m else {}
        except ValueError:
            data = {}
        text = str(data.get("text") or "").strip()
        reason = str(data.get("reason") or "").strip()
        if not data.get("post") or not text:
            logger.info("[qzone_ai] 模型这次不想发")
            st["last_result"] = time.strftime("%m-%d %H:%M") + " 模型决定不发" + ("：" + reason if reason else "")
            save(STATE, st)
            return
        if not forced and st.get("last_post_date") == time.strftime("%Y-%m-%d"):
            logger.info("[qzone_ai] 今天已经发过，跳过")
            st["last_result"] = time.strftime("%m-%d %H:%M") + " 今天已经发过，跳过"
            save(STATE, st)
            return

        mode = st.get("mode") or "both"
        need_review = bool(data.get("review")) if mode == "both" else (mode == "draft")
        if mode == "auto" or (mode == "both" and not need_review):
            ok = await self._publish(text)
            if ok:
                st["last_post_date"] = time.strftime("%Y-%m-%d")
                st["last_result"] = time.strftime("%m-%d %H:%M") + " 已发布"
            else:
                st["last_result"] = time.strftime("%m-%d %H:%M") + " 发布失败"
            save(STATE, st)
        else:
            drafts = load(DRAFTS, [])
            drafts.append({"id": str(int(time.time())), "time": time.strftime("%m-%d %H:%M"),
                           "text": text, "reason": reason})
            save(DRAFTS, drafts[-20:])
            st["last_result"] = time.strftime("%m-%d %H:%M") + " 已存草稿"
            save(STATE, st)
            logger.info(f"[qzone_ai] 存了一条草稿：{text}（原因：{reason}）")

    async def _diary(self, st: dict) -> None:
        st["force_diary"] = False
        chat = today_chat()
        if len(chat.splitlines()) < 5:
            logger.info("[qzone_ai] 今天聊得太少，日记跳过")
            st["last_diary_result"] = time.strftime("%m-%d %H:%M") + " 聊天太少，跳过"
            save(STATE, st)
            return
        provider = self.context.get_using_provider()
        if not provider:
            logger.warning("[qzone_ai] 没有可用的对话模型")
            st["last_diary_result"] = time.strftime("%m-%d %H:%M") + " 没有可用模型"
            save(STATE, st)
            return
        try:
            resp = await provider.text_chat(
                prompt=DIARY_PROMPT + "\n\n今天的聊天：\n" + chat,
                system_prompt="你是大肥鱼，写日记要短、口语、像真人。只输出要求的 JSON。",
            )
            raw = (resp.completion_text or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[qzone_ai] 日记模型调用失败: {exc}")
            st["last_diary_result"] = time.strftime("%m-%d %H:%M") + " 模型调用失败"
            save(STATE, st)
            return
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            data = json.loads(m.group(0)) if m else {}
        except ValueError:
            data = {}
        text = str(data.get("text") or "").strip()
        if not data.get("post") or not text:
            logger.info("[qzone_ai] 日记没写出来")
            st["last_diary_result"] = time.strftime("%m-%d %H:%M") + " 模型没写出来"
            save(STATE, st)
            return
        ok = await self._publish(text)
        if ok:
            st["last_diary_date"] = time.strftime("%Y-%m-%d")
            st["last_diary_result"] = time.strftime("%m-%d %H:%M") + " 已发布"
            logger.info(f"[qzone_ai] 日记已发：{text[:40]}…")
        else:
            st["last_diary_result"] = time.strftime("%m-%d %H:%M") + " 发布失败"
        save(STATE, st)

    async def _publish(self, text: str) -> bool:
        try:
            r = await asyncio.to_thread(
                subprocess.run,
                [PY, "-X", "utf8", str(PUBLISH), "--text", text],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            )
            logger.info(f"[qzone_ai] 发布结果: {r.stdout.strip()[:200]} {r.stderr.strip()[:200]}")
            return '"ok": true' in (r.stdout or "").lower()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[qzone_ai] 发布失败: {exc}")
            return False


@register
class QzoneModule(Module):
    name = "qzone"
    description = "AI 说说和每日日记"

    async def setup(self) -> None:
        self.engine = QzoneEngine()
        self.engine.context = self.kernel.context
        await self.engine.initialize()
