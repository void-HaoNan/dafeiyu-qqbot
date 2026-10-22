"""大肥鱼耳朵 v1 · 收到的语音先转成文字

QQ 语音是 SILK/AMR，主模型（DeepSeek）不认音频。原样塞进请求会 422，
整条消息直接没回复。这里在 LLM 请求前用本机 faster-whisper 转成文字，
把语音消息换成普通文本，模型看到的就是一句话。

取舍：
- 用本机小模型，不上传语音、不额外花钱；第一次用时下载约 460MB
- 转写失败只留一句「没听清」，不让整条消息挂掉
- 模型加载失败只影响语音，文字聊天照常
"""

from __future__ import annotations

import asyncio
import io
import os
import pathlib
import re
import shutil
import subprocess
import time
import uuid
import wave

from astrbot.api import logger
from astrbot.api.message_components import Plain, Record, Reply

from dafeiyu_core.module import Module, register

try:
    from dafeiyu_core.paths import ROOT
except Exception:  # noqa: BLE001
    ROOT = pathlib.Path(os.environ.get("QQBOT_ROOT", r"D:\QQBot"))

TMP_DIR = ROOT / "logs" / "asr"
DEFAULT_MODEL = ROOT / "models" / "asr" / "faster-whisper-small"
DEFAULT_REPO = "Systran/faster-whisper-small"
DEFAULT_MIRROR = "https://hf-mirror.com"

# Whisper 在静音/噪声上常见的幻觉句，遇到就当没听清
_HALLUCINATIONS = (
    "谢谢观看",
    "谢谢大家",
    "请不吝点赞",
    "字幕由",
    "字幕提供",
    "明镜与点点",
)


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ffmpeg() -> str:
    local = ROOT / "bin" / "ffmpeg.exe"
    if local.exists():
        return str(local)
    return shutil.which("ffmpeg") or "ffmpeg"


def _silk_to_wav(src: pathlib.Path, dst: pathlib.Path) -> None:
    """QQ 的 SILK 语音 ffmpeg 解不了，先用 pysilk 解成 24k wav。"""
    import pysilk

    data = src.read_bytes()
    if data.startswith(b"\x02"):
        data = data[1:]
    out = io.BytesIO()
    pysilk.decode(io.BytesIO(data), out, 24000)
    pcm = out.getvalue()
    if not pcm:
        raise RuntimeError("SILK 解码出来是空的")
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(pcm)


def _to_16k(src: pathlib.Path, dst: pathlib.Path) -> None:
    r = subprocess.run(
        [
            _ffmpeg(),
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(dst),
        ],
        capture_output=True,
        timeout=120,
    )
    if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 1000:
        detail = (r.stderr or b"")[-200:].decode("utf-8", "ignore")
        raise RuntimeError(f"ffmpeg 转 16k 失败: {detail}")


def _read_wav16(path: pathlib.Path):
    import numpy as np

    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2 or rate != 16000 or channels != 1:
        raise RuntimeError(f"wav 格式不对: {rate}Hz {channels}ch {width*8}bit")
    return np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0


def _clean(text: str) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    t = t.strip("，。！？、,.!?~～ ")
    if not t:
        return ""
    low = t.lower()
    if low in _HALLUCINATIONS or low.startswith("字幕") or "amara.org" in low:
        return ""
    return t[:500]


class AsrEngine:
    def __init__(self, cfg: dict) -> None:
        self.model_dir = pathlib.Path(
            str(cfg.get("model_dir") or DEFAULT_MODEL)
        )
        self.repo_id = str(cfg.get("repo_id") or DEFAULT_REPO)
        self.language = str(cfg.get("language") or "zh")
        self.compute_type = str(cfg.get("compute_type") or "int8")
        self.cpu_threads = _int(cfg.get("cpu_threads"), 8)
        self.beam_size = _int(cfg.get("beam_size"), 5)
        self.timeout = _int(cfg.get("timeout_seconds"), 90)
        self.auto_download = bool(cfg.get("auto_download", True))
        self.hf_endpoint = str(cfg.get("hf_endpoint") or DEFAULT_MIRROR)
        self._model = None
        self._load_lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
        self._fail_until = 0.0

    # ---------- 模型 ----------

    def _download(self) -> str:
        from huggingface_hub import snapshot_download

        logger.info(f"[asr] 首次使用，下载语音识别模型 {self.repo_id} ...")
        return snapshot_download(
            self.repo_id,
            local_dir=str(self.model_dir),
            endpoint=self.hf_endpoint or None,
        )

    def _load(self):
        from faster_whisper import WhisperModel

        path = self.model_dir
        if not (path / "model.bin").exists():
            if not self.auto_download:
                raise RuntimeError(f"语音模型不存在：{path}")
            path = pathlib.Path(self._download())
        logger.info(f"[asr] 加载语音识别模型：{path}")
        model = WhisperModel(
            str(path),
            device="cpu",
            compute_type=self.compute_type,
            cpu_threads=self.cpu_threads,
        )
        logger.info("[asr] 语音识别模型就绪")
        return model

    async def _ensure_model(self):
        if self._model is not None:
            return self._model
        if time.time() < self._fail_until:
            raise RuntimeError("语音模型上次加载失败，稍后再试")
        async with self._load_lock:
            if self._model is not None:
                return self._model
            try:
                self._model = await asyncio.to_thread(self._load)
            except Exception:
                self._fail_until = time.time() + 60
                raise
            return self._model

    # ---------- 转写 ----------

    def _transcribe_sync(self, model, src: pathlib.Path) -> str:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        silk_wav = TMP_DIR / f"{token}.silk.wav"
        wav16 = TMP_DIR / f"{token}.16k.wav"
        cleanup = [silk_wav, wav16]
        try:
            with open(src, "rb") as f:
                head = f.read(16)
            source = src
            if head.startswith(b"\x02#!SILK_V3") or head.startswith(b"#!SILK_V3"):
                _silk_to_wav(src, silk_wav)
                source = silk_wav
            _to_16k(source, wav16)
            audio = _read_wav16(wav16)
            if len(audio) < 3200:
                return ""
            segments, _info = model.transcribe(
                audio,
                language=self.language or None,
                beam_size=self.beam_size,
                condition_on_previous_text=False,
                vad_filter=False,
            )
            text = "".join(seg.text for seg in segments)
            return _clean(text)
        finally:
            for p in cleanup:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass

    async def transcribe(self, src: str) -> str:
        path = pathlib.Path(str(src))
        if not path.exists():
            return ""
        async with self._run_lock:
            model = await self._ensure_model()
            return await asyncio.wait_for(
                asyncio.to_thread(self._transcribe_sync, model, path),
                timeout=self.timeout,
            )


@register
class AsrModule(Module):
    name = "asr"
    description = "收到语音先转文字，主模型不认音频也不会整条挂掉"
    priority = 10

    def __init__(self, kernel) -> None:
        super().__init__(kernel)
        self.engine = AsrEngine(self.cfg)

    async def setup(self) -> None:
        logger.info("[asr] 语音转文字已加载")

    async def _hear(self, comp) -> str:
        try:
            path = await comp.convert_to_file_path()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[asr] 取语音文件失败：{exc}")
            return ""
        if not path:
            return ""
        try:
            text = await self.engine.transcribe(path)
        except asyncio.TimeoutError:
            logger.warning("[asr] 转写超时")
            return ""
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[asr] 转写失败：{exc}")
            return ""
        if text:
            logger.info(f"[asr] 听到：{text[:60]}")
        return text

    @staticmethod
    def _label(text: str, quoted: bool) -> str:
        prefix = "[引用语音]" if quoted else "[语音]"
        return f"{prefix} {text}" if text else f"{prefix} 没听清"

    async def on_waiting_llm_request(self, event) -> None:
        """在 AstrBot 组装请求之前，把语音换成文字。

        必须用 on_waiting_llm_request：等到 on_llm_request 时，AstrBot 已经把
        音频组装进 messages 了，再清 audio_urls 也来不及。
        """
        try:
            comps = event.message_obj.message
        except Exception:  # noqa: BLE001
            return
        if not comps:
            return
        if not isinstance(comps, list):
            comps = list(comps)
            event.message_obj.message = comps

        notes: list[str] = []
        heard: list[str] = []
        changed = False
        for idx, comp in enumerate(list(comps)):
            if isinstance(comp, Record):
                text = await self._hear(comp)
                label = self._label(text, quoted=False)
                comps[idx] = Plain(text=label)
                notes.append(label)
                if text:
                    heard.append(label)
                changed = True
            elif isinstance(comp, Reply) and getattr(comp, "chain", None):
                for jdx, sub in enumerate(list(comp.chain)):
                    if isinstance(sub, Record):
                        text = await self._hear(sub)
                        label = self._label(text, quoted=True)
                        comp.chain[jdx] = Plain(text=label)
                        notes.append(label)
                        if text:
                            heard.append(label)
                        changed = True

        if not changed:
            return
        try:
            base = event.message_str or ""
            event.message_str = (base + " " + " ".join(notes)).strip()
            event.message_obj.message_str = event.message_str
        except Exception:  # noqa: BLE001
            pass
        # 消息流/自动记忆都从 panel_stream.jsonl 读；语音不补一条，转写内容就丢了
        try:
            from dafeiyu_core import stream

            for label in heard:
                stream.write(event, label, "in", 0)
        except Exception:  # noqa: BLE001
            pass
