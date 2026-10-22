"""大肥鱼语音 v7 · 全交给模型判断

不再用正则猜「要不要发语音」「是什么情绪」。

模型在回复时自己决定调不调 `send_voice`：
    send_voice(text="本鱼在的呀", emotion="开心")

- text     要说的内容（照完整说，别为了省字数把话砍短）
- emotion  情绪，决定用哪组参考音频

合成链路：清洗 → 按情绪选参考音频（3~10 秒）→ GPT-SoVITS → 峰值归一化 → 发语音
GPT-SoVITS 挂了会回落 edge-tts。

语音开关由模型调用 set_voice_enabled 工具，不再匹配命令。
"""

import os
import pathlib
import random
import re
import time

try:
    from dafeiyu_core.paths import ROOT
except Exception:  # noqa: BLE001
    ROOT = pathlib.Path(os.environ.get("QQBOT_ROOT", r"D:\QQBot"))
_FFMPEG_DIR = str(ROOT / "bin")
if os.path.isdir(_FFMPEG_DIR) and _FFMPEG_DIR not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")

import asyncio
import json

import edge_tts
import requests

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain, Record
from dafeiyu_core.module import Module, ToolSpec, register

OUT_DIR = ROOT / "logs" / "voice"
OUT_DIR.mkdir(parents=True, exist_ok=True)
STREAM = ROOT / "logs" / "panel_stream.jsonl"
CONF = ROOT / "AstrBot" / "data" / "config" / "astrbot_plugin_dafeiyu_voice_config.json"
PANEL_STATE = ROOT / "panel" / "model_state.json"
PY = str(ROOT / "AstrBot" / ".venv" / "Scripts" / "python.exe")
SING_SCRIPT = str(ROOT / "scripts" / "sing_song.py")
SING_OUT_DIR = ROOT / "rvc" / "out"
MAX_SING_SECONDS = 45


def _to_mp3(src: pathlib.Path) -> pathlib.Path:
    """QQ 语音条不认 wav，转成 mp3 再发。"""
    import subprocess

    if src.suffix.lower() in {".mp3", ".silk", ".amr"}:
        return src
    ffmpeg = os.path.join(_FFMPEG_DIR, "ffmpeg.exe")
    out = src.with_suffix(".mp3")
    try:
        r = subprocess.run(
            [ffmpeg, "-y", "-i", str(src), "-c:a", "libmp3lame", "-q:a", "4", "-ar", "44100", str(out)],
            capture_output=True, timeout=120,
        )
        if r.returncode == 0 and out.exists() and out.stat().st_size > 2000:
            return out
        logger.warning(f"[voice] 转 mp3 失败: {r.stderr[-160:]!r}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[voice] 转 mp3 异常: {exc}")
    return src


def _log_stream(event, text: str) -> None:
    """直接发的语音不走回复链，补一条让控制台能看到。"""
    try:
        gid = str(event.get_group_id() or "")
        rec = {
            "ts": time.time(),
            "dir": "out",
            "conv_id": gid or str(event.get_sender_id() or ""),
            "is_group": bool(gid),
            "sender": "大肥鱼",
            "uid": str(event.get_sender_id() or ""),
            "umo": event.unified_msg_origin,
            "text": text,
            "images": 0,
        }
        with STREAM.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[voice] 写消息流失败: {exc}")

# 情绪名 → 文件夹别名（文件夹用中文名，插件按子串匹配）
EMOTION_FOLDERS = {
    "开心": ("开心", "happy", "高兴"),
    "激动": ("激动", "excited"),
    "生气": ("生气", "angry", "愤怒"),
    "难过": ("难过", "sad", "伤心"),
    "害羞": ("害羞", "shy"),
    "撒娇": ("撒娇", "coquettish"),
    "疑惑": ("疑惑", "confused"),
    "委屈": ("委屈", "grievance"),
    "惊讶": ("惊讶", "surprised"),
    "中立": ("中立", "neutral", "平静"),
}
MIN_SEC, MAX_SEC = 3.0, 10.0

DEFAULTS = {
    "voice_enable": True,
    "use_gpt_sovits": True,
    "gsv_url": "http://127.0.0.1:9880",
    "gsv_ref_audio": "",
    "gsv_prompt_text": "",
    "emotion_ref_enable": True,
    "emotion_ref_root": "",
    "edge_voice": "zh-CN-XiaoyiNeural",
    "max_chars": 60,
}


def _unified() -> dict:
    try:
        root = pathlib.Path(os.environ.get("QQBOT_ROOT", str(ROOT)))
        data = json.loads((root / "data" / "dafeiyu" / "config.json").read_text(encoding="utf-8"))
        return (data.get("modules") or {}).get("voice") or {}
    except Exception:  # noqa: BLE001
        return {}


def conf() -> dict:
    out = dict(DEFAULTS)
    try:
        out.update({k: v for k, v in json.loads(CONF.read_text(encoding="utf-8-sig")).items() if v is not None})
    except Exception:  # noqa: BLE001
        pass
    try:
        out.update({k: v for k, v in _unified().items() if v is not None})
    except Exception:  # noqa: BLE001
        pass
    return out


def save_conf(data: dict) -> None:
    try:
        CONF.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[voice] 保存配置失败: {exc}")


def _panel_state() -> dict:
    try:
        return json.loads(PANEL_STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _clean_for_tts(text: str) -> str:
    """去掉不该念出来的：括号动作、表情、markdown。"""
    t = text or ""
    for _ in range(3):
        new = re.sub(r"[（(][^（）()]{0,80}[）)]", "", t)
        if new == t:
            break
        t = new
    t = re.sub(r"【[^】]{0,40}】|\[[^\]]{0,40}\]|<[^>]{0,40}>", "", t)
    t = re.sub(r"[*_`#>~|\\]+", "", t)
    t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", t)
    t = re.sub(r"[～~]{2,}", "，", t)
    t = re.sub(r"\.{2,}|。{2,}", "……", t)
    t = re.sub(r"\s*\n+\s*", "，", t)
    t = re.sub(r"[，,]{2,}", "，", t)
    return t.strip(" ，,、；;：:…·-—")


def _wav_seconds(path: pathlib.Path) -> float:
    try:
        import wave as _wave

        with _wave.open(str(path)) as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:  # noqa: BLE001
        try:
            return path.stat().st_size / 32000.0
        except Exception:  # noqa: BLE001
            return 0.0


def _pick_ref(emotion: str, cfg: dict) -> tuple[str, str] | None:
    """按情绪挑一条 3~10 秒的参考音频，返回 (路径, 台词)。"""
    state = _panel_state()
    root = pathlib.Path(str(state.get("emotion_root") or cfg.get("emotion_ref_root") or ""))
    if not root.is_dir():
        return None

    aliases = EMOTION_FOLDERS.get(emotion) or EMOTION_FOLDERS["中立"]
    dirs = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        low = d.name.lower()
        if any(a.lower() in low for a in aliases):
            dirs.append(d)
    # 找不到对应情绪就回落到中立
    if not dirs:
        for d in root.iterdir():
            if d.is_dir() and any(a.lower() in d.name.lower() for a in EMOTION_FOLDERS["中立"]):
                dirs.append(d)
    if not dirs:
        return None

    wavs = []
    for d in dirs:
        for p in d.glob("*.wav"):
            if MIN_SEC <= _wav_seconds(p) <= MAX_SEC:
                wavs.append(p)
    if not wavs:
        return None
    pick = random.choice(wavs)
    prompt = re.sub(r"^【[^】]*】", "", pick.stem).strip(" 　…·-—")
    if not prompt:
        return None
    logger.info(f"[voice] 情绪={emotion} 参考={pick.parent.name}/{pick.name[:20]}")
    return str(pick), prompt


def _gsv(text: str, path: pathlib.Path, cfg: dict, emotion: str) -> bool:
    state = _panel_state()
    ref = str(state.get("ref_audio") or cfg.get("gsv_ref_audio") or "")
    prompt_text = state.get("prompt") or cfg.get("gsv_prompt_text") or ""
    prompt_lang = "zh"

    if cfg.get("emotion_ref_enable"):
        picked = _pick_ref(emotion, cfg)
        if picked:
            ref, prompt_text = picked
    if not ref or not pathlib.Path(ref).exists():
        return False

    try:
        r = requests.post(
            str(cfg["gsv_url"]).rstrip("/") + "/tts",
            json={
                "text": text[: int(cfg["max_chars"])],
                "text_lang": "ja" if re.search(r"[\u3040-\u30ff]", text) else "zh",
                "ref_audio_path": ref,
                "prompt_text": prompt_text,
                "prompt_lang": prompt_lang,
                "media_type": "wav",
            },
            timeout=180,
        )
        if r.status_code == 200 and len(r.content) > 4000:
            path.write_bytes(r.content)
            return True
        logger.warning(f"[voice] GPT-SoVITS {r.status_code}: {r.text[:120]}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[voice] GPT-SoVITS 不可用: {exc}")
    return False


async def _edge(text: str, path: pathlib.Path, cfg: dict) -> bool:
    try:
        await edge_tts.Communicate(text, cfg["edge_voice"]).save(str(path))
        return path.exists() and path.stat().st_size > 800
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[voice] edge-tts 失败: {exc}")
        return False


def _normalize(src: pathlib.Path, target_db: float = -2.0) -> pathlib.Path:
    """峰值归一化到 -2 dB。"""
    import subprocess

    ffmpeg = os.path.join(_FFMPEG_DIR, "ffmpeg.exe")
    try:
        probe = subprocess.run(
            [ffmpeg, "-i", str(src), "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
        m = re.search(r"max_volume:\s*(-?[\d.]+) dB", probe.stderr or "")
        if not m:
            return src
        peak = float(m.group(1))
        gain = min(target_db - peak, 30.0)
        if gain <= 1.0:
            return src
        out = src.with_name(src.stem + "_norm" + src.suffix)
        r = subprocess.run(
            [ffmpeg, "-y", "-i", str(src), "-af", f"volume={gain:.1f}dB,alimiter=limit=0.95",
             "-ar", "32000", str(out)],
            capture_output=True, timeout=120,
        )
        if r.returncode == 0 and out.exists():
            return out
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[voice] 归一化异常: {exc}")
    return src


class VoiceEngine:
    def __init__(self) -> None:
        self._sing_busy = asyncio.Lock()

    async def _make(self, text: str, emotion: str) -> pathlib.Path | None:
        cfg = conf()
        clean = _clean_for_tts(text)
        if len(clean) < 2:
            clean = "本鱼在的呀。"
        clean = clean[: int(cfg["max_chars"])]
        path = OUT_DIR / f"say_{int(time.time()*1000)}.wav"
        if cfg.get("use_gpt_sovits") and await asyncio.to_thread(_gsv, clean, path, cfg, emotion):
            return await asyncio.to_thread(_normalize, path)
        mp3 = path.with_suffix(".mp3")
        if await _edge(clean, mp3, cfg):
            return mp3
        return None

    async def send_voice(self, event: AstrMessageEvent, text: str, emotion: str = "中立"):
        """用声音说话。当一句话更适合被听见、或者对方要你用语音时调用。

        重要：对方说「用 XX 语气说一句」「用语音说」「说句话」「念一段」「唱不了就用说的」时，
        一定要调用这个工具，不要只回文字。emotion 就填他说的那个 XX。

        不要每句都调，挑合适的时机。要说就完整说，别为了省字数把意思砍掉。

        Args:
            text(string): 要说的话，可以是一整段，去掉括号动作
            emotion(string): 说这句话的情绪，从这些里选一个：开心 激动 生气 难过 害羞 撒娇 疑惑 委屈 惊讶 中立
        """
        cfg = conf()
        if not cfg.get("voice_enable"):
            return "语音功能现在是关的。不要自动打开；请先提醒用户语音关着，问要不要打开。"
        emotion = (emotion or "中立").strip()
        if emotion not in EMOTION_FOLDERS:
            emotion = "中立"
        path = await self._make(text, emotion)
        if not path:
            return "语音没合成出来"
        path = await asyncio.to_thread(_to_mp3, path)
        try:
            await event.send(MessageChain([Record.fromFileSystem(str(path))]))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[voice] 发送失败: {exc}")
            return f"发送失败：{exc}"
        logger.info(f"[voice] 模型调用语音，情绪={emotion}，{len(_clean_for_tts(text))} 字")
        _log_stream(event, f"[语音·{emotion}]{text[:30]}")
        return f"已发送语音（情绪：{emotion}）"

    async def sing_song(self, event: AstrMessageEvent, song: str):
        """唱歌。用户让你「唱」某首歌、点名听你唱的时候必须用这个工具。

        它和 play_song_by_name 不一样：那个只是把原曲放出来，这个是把歌换成
        本鱼的声音真的唱一遍。所以只要用户说的是「唱」——唱《X》、你唱给我听、
        来一首你唱的、唱首歌——一律用 sing_song，绝对不要用点歌工具代替。
        调完这个工具歌就直接发过去了，不用再调点歌。

        Args:
            song(string): 歌名，比如「打上花火」
        """
        song = (song or "").strip()
        if not song:
            return "没说要唱哪首"

        if self._sing_busy.locked():
            return "正在唱另一首，让用户等一下"

        async with self._sing_busy:
            out = SING_OUT_DIR / f"sing_{int(time.time())}.mp3"
            cmd = [PY, "-X", "utf8", SING_SCRIPT, "--song", song, "--out", str(out),
                   "--max-seconds", str(MAX_SING_SECONDS)]
            logger.info(f"[sing] 开始：{song}")
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
            except asyncio.TimeoutError:
                return "唱到一半超时了，没唱完"
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[sing] 启动失败: {exc}")
                return f"唱歌工具启动失败：{exc}"

            text = (stdout or b"").decode("utf-8", "ignore")
            if proc.returncode != 0 or not out.exists():
                logger.warning(f"[sing] 失败：{text[-800:]}")
                if "拿不到免费音频" in text:
                    return "这首在网易云要会员或者没版权，换一首吧"
                if "没分离出人声" in text:
                    return "这首分离出来没有人声，可能是纯音乐或伴奏"
                return "这次没唱出来（可能是歌没搜到或者没版权）"

            try:
                await event.send(MessageChain([Record.fromFileSystem(str(out))]))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[sing] 发送失败: {exc}")
                return f"唱好了但发送失败：{exc}"

            logger.info(f"[sing] 唱完：{out.name} {out.stat().st_size // 1024}KB")
            return f"已经把《{song}》唱完发过去了（前 {MAX_SING_SECONDS} 秒），快夸本鱼"

    async def set_voice_enabled(self, event, enabled: bool = True) -> str:
        cfg = conf()
        cfg["voice_enable"] = bool(enabled)
        save_conf(cfg)
        try:
            p = ROOT / "data" / "dafeiyu" / "config.json"
            data = json.loads(p.read_text(encoding="utf-8"))
            data.setdefault("modules", {}).setdefault("voice", {})["voice_enable"] = bool(enabled)
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        return f"语音已{'打开' if enabled else '关闭'}"


@register
class VoiceModule(Module):
    name = "voice"
    description = "语音说话 + 唱歌"

    async def setup(self) -> None:
        self.engine = VoiceEngine()
        self._tasks = set()

    def tools(self):
        return [
            ToolSpec(
                "send_voice",
                "用声音说话。对方要你用语音、或者一句话更适合被听见时调用。",
                {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "要说的话"},
                        "emotion": {"type": "string", "description": "情绪", "default": "中立"},
                    },
                    "required": ["text"],
                },
                self.engine.send_voice,
            ),
            ToolSpec(
                "sing_song",
                "唱歌。用户让你唱某首歌时用，不要用点歌工具代替。",
                {
                    "type": "object",
                    "properties": {"song": {"type": "string", "description": "歌名"}},
                    "required": ["song"],
                },
                self._sing,
            ),
            ToolSpec(
                "set_voice_enabled",
                "只在用户明确说要打开或关闭语音时调用。用户只是要求语音回复、而语音当前是关的，不要调用它去打开，先提醒用户语音关着。",
                {
                    "type": "object",
                    "properties": {"enabled": {"type": "boolean", "description": "true 打开，false 关闭"}},
                    "required": ["enabled"],
                },
                self.engine.set_voice_enabled,
            ),
        ]

    async def _sing(self, event, song: str = ""):
        song = (song or "").strip()
        if not song:
            return "没说要唱哪首"
        task = asyncio.create_task(self.engine.sing_song(event, song))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return "已开始后台合成，完成后会直接发语音。"
