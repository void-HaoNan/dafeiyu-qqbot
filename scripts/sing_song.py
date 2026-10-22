#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""让大肥鱼唱歌：点歌 → 分离人声 → RVC 换音色 → 和伴奏混回去。

用 AstrBot 的 venv 跑（只需要标准库），重活交给 RVC 整合包自带的 runtime。

  python scripts/sing_song.py --song "打上花火" --out D:\\QQBot\\rvc\\out\\sing.mp3

流程：
  1. 网易云官方接口搜歌、拿直链
  2. 下载 mp3
  3. demucs 分离 vocoder/no_vocals（比之前那个 karaoke 模型稳）
  4. 在人声最响的地方截一段（默认 45 秒），避免截到纯前奏
  5. RVC 把人声换成唱歌模型
  6. ffmpeg 混音、导出 mp3
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import wave
from pathlib import Path

def _load_paths():
    root = Path(os.environ.get("QQBOT_ROOT") or Path(__file__).resolve().parents[1])
    try:
        data = json.loads((root / "data" / "dafeiyu" / "config.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        data = {}
    return (data.get("paths") or {}), root


_PATHS, ROOT = _load_paths()
_RVC_ROOT = Path(_PATHS.get("rvc_root") or (ROOT / "rvc"))


def _detect_pkg() -> Path:
    if _PATHS.get("rvc_pkg"):
        return Path(_PATHS["rvc_pkg"])
    for py in sorted(_RVC_ROOT.glob("pkg/*/runtime/python.exe")):
        return py.parent.parent
    return _RVC_ROOT


RVC_DIR = _detect_pkg()
RVC_PY = RVC_DIR / "runtime" / "python.exe"
TORCH_HOME = Path(_PATHS.get("torch_home") or (_RVC_ROOT / "models" / "torch"))
FFMPEG = Path(_PATHS.get("ffmpeg") or (ROOT / "bin" / "ffmpeg.exe"))
WORK = Path(_PATHS.get("work_dir") or (_RVC_ROOT / "out" / "work"))
RVC_SCRIPT = Path(_PATHS.get("rvc_script") or (ROOT / "scripts" / "rvc_convert.py"))
MODEL_MAP = Path(_PATHS.get("model_map") or (_RVC_ROOT / "model_map.json"))

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# 网易云官方接口拿不到（会员/版权）时，用这个解析源兜底
GD_BASE = "https://music-api.gdstudio.xyz/api.php"


def log(msg: str) -> None:
    print(f"[sing] {msg}", flush=True)


def http_json(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://music.163.com/"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def search_songs(name: str, limit: int = 8) -> list[dict]:
    """搜一串候选，过滤掉伴奏/纯音乐。"""
    q = urllib.parse.quote(name)
    data = http_json(f"https://music.163.com/api/search/get/web?s={q}&type=1&limit={limit}")
    songs = (data.get("result") or {}).get("songs") or []
    out = []
    for s in songs:
        title = s.get("name") or ""
        if "伴奏" in title or "纯音乐" in title or "off vocal" in title.lower():
            continue
        out.append({"id": s["id"], "name": title or name})
    if not out and songs:
        out.append({"id": songs[0]["id"], "name": songs[0].get("name", name)})
    return out


def song_url(song_id: int) -> str:
    """拿直链；320k 不行就降码率再试，都没有就返回空串。"""
    for br in (320000, 192000, 128000):
        try:
            data = http_json(
                f"https://music.163.com/api/song/enhance/player/url?ids=%5B{song_id}%5D&br={br}"
            )
        except Exception:  # noqa: BLE001
            continue
        items = data.get("data") or []
        url = items[0].get("url") if items else None
        if url:
            return url
    return ""


def gd_url(song_id: int, source: str = "netease") -> str:
    """第三方解析源，能拿到官方接口要会员的那些歌。"""
    try:
        data = http_json(f"{GD_BASE}?types=url&source={source}&id={song_id}&br=320", timeout=25)
    except Exception:  # noqa: BLE001
        return ""
    return data.get("url") or ""


def _clean_title(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def bilibili_search(keyword: str, limit: int = 5) -> list[dict]:
    """B 站搜视频，给音乐库里没有的梗/翻唱兜底。"""
    q = urllib.parse.quote(keyword)
    url = f"https://api.bilibili.com/x/web-interface/wbi/search/type?search_type=video&keyword={q}&page=1"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as exc:  # noqa: BLE001
        log(f"B 站搜索失败：{exc}")
        return []
    out = []
    for item in (data.get("data") or {}).get("result") or []:
        bvid = item.get("bvid")
        if bvid:
            out.append({"bvid": bvid, "title": _clean_title(item.get("title", ""))})
        if len(out) >= limit:
            break
    return out


def bilibili_download(bvid: str, out_dir: Path) -> Path | None:
    """用 yt-dlp 把 B 站视频的音频拉下来。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "yt_dlp", "-x", "--audio-format", "mp3", "--audio-quality", "0",
        "--no-playlist", "-o", str(out_dir / "%(id)s.%(ext)s"),
        f"https://www.bilibili.com/video/{bvid}",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        log(f"B 站下载失败：{(r.stderr or '')[-300:]}")
        return None
    files = sorted(out_dir.glob(f"{bvid}.*"))
    return files[0] if files else None


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[\s《》〈〉()（）\[\]【】\-_/|·]+", s or "") if len(t) >= 2]


def matches_title(query: str, title: str) -> bool:
    """查询里的关键词都得出现在标题里，免得拿原曲糊弄「司凤版」这种。"""
    toks = _tokens(query)
    return all(t in (title or "") for t in toks) if toks else True


def guess_lang(text: str) -> str:
    kana = len(re.findall(r"[\u3040-\u30ff]", text or ""))
    han = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    if kana + han == 0:
        return "unknown"
    return "ja" if kana > 0.2 * (kana + han) else "zh"


def download(url: str, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://music.163.com/"})
    with urllib.request.urlopen(req, timeout=90) as r, open(out, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return out


def separate(audio: Path) -> tuple[Path, Path]:
    """demucs 分离，返回 (vocals, no_vocals)。"""
    out_dir = WORK / "demucs"
    env = {
        **os.environ,
        "TORCH_HOME": str(TORCH_HOME),
        "PYTHONIOENCODING": "utf-8",
        # 走镜像，免得 demucs 每次去 huggingface.co 超时重试
        "HF_ENDPOINT": "https://hf-mirror.com",
    }
    subprocess.run(
        [
            str(RVC_PY), "-m", "demucs.separate", "--two-stems=vocals",
            "-n", "htdemucs", "--device", "cuda",
            "-o", str(out_dir), str(audio),
        ],
        check=True, env=env, cwd=str(RVC_DIR),
    )
    base = out_dir / "htdemucs" / audio.stem
    vocals = base / "vocals.wav"
    inst = base / "no_vocals.wav"
    if not vocals.exists() or not inst.exists():
        raise RuntimeError(f"分离结果不对：{list(base.glob('*')) if base.exists() else base}")
    return vocals, inst


def _rms_per_second(path: Path) -> list[float]:
    """每秒一个平均振幅。不是 16bit 就先转一份，省得解析各种 wav 格式。"""
    pcm = path
    try:
        with wave.open(str(path), "rb") as w:
            ok = w.getsampwidth() == 2
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        pcm = WORK / "analysis.wav"
        subprocess.run(
            [str(FFMPEG), "-y", "-loglevel", "error", "-i", str(path),
             "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(pcm)],
            check=True,
        )
    with wave.open(str(pcm), "rb") as w:
        sr = w.getframerate()
        frames = w.getnframes()
        n = w.getnchannels()
        raw = w.readframes(frames)
    import array

    samples = array.array("h")
    samples.frombytes(raw)
    if n > 1:
        samples = samples[::n]
    step = max(1, sr)
    out = []
    for i in range(0, len(samples), step):
        chunk = samples[i:i + step]
        if not chunk:
            break
        out.append(sum(abs(x) for x in chunk) / len(chunk))
    return out


def best_window(vocals: Path, seconds: int) -> float:
    """找一个人声最响的起点，别截到纯前奏。"""
    env = _rms_per_second(vocals)
    if not env:
        return 0.0
    win = min(seconds, max(1, len(env)))
    best_i, best_sum = 0, -1.0
    for i in range(0, max(1, len(env) - win + 1)):
        s = sum(env[i:i + win])
        if s > best_sum:
            best_sum, best_i = s, i
    return float(best_i)


def cut_pair(vocals: Path, inst: Path, start: float, seconds: int) -> tuple[Path, Path]:
    v = WORK / "cut_vocals.wav"
    i = WORK / "cut_inst.wav"
    for src, dst in ((vocals, v), (inst, i)):
        subprocess.run(
            [str(FFMPEG), "-y", "-loglevel", "error", "-ss", str(start), "-t", str(seconds),
             "-i", str(src), "-ar", "44100", str(dst)],
            check=True,
        )
    return v, i


def convert(vocals: Path, model: str, index: str, out: Path, pitch: int) -> None:
    subprocess.run(
        [str(RVC_PY), str(RVC_SCRIPT), "--input", str(vocals), "--output", str(out),
         "--model", model, "--index", index, "--pitch", str(pitch)],
        check=True, cwd=str(RVC_DIR),
    )


def mix(vocals: Path, inst: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(FFMPEG), "-y", "-loglevel", "error",
            "-i", str(inst), "-i", str(vocals),
            "-filter_complex",
            # 人声提亮一点、伴奏压到 0.45，人声才不会被盖住（9 号对照实验选出来的）
            "[0:a]volume=0.45[i];[1:a]volume=1.0,treble=g=4[v];"
            "[i][v]amix=inputs=2:duration=shortest,volume=2,alimiter=limit=0.95",
            "-ar", "44100", "-b:a", "192k", str(out),
        ],
        check=True,
    )


def detect_language(song_id: int) -> str:
    """按歌词判断语种：假名多就是日语，汉字多就是中文。"""
    try:
        data = http_json(f"https://music.163.com/api/song/lyric?id={song_id}&lv=1&kv=1&tv=-1")
        lrc = (data.get("lrc") or {}).get("lyric") or ""
    except Exception:  # noqa: BLE001
        return "unknown"
    kana = len(re.findall(r"[\u3040-\u30ff]", lrc))
    han = len(re.findall(r"[\u4e00-\u9fff]", lrc))
    if kana + han < 20:
        return "unknown"
    return "ja" if kana > 0.2 * (kana + han) else "zh"


def pick_model(lang: str, explicit_model: str, explicit_index: str) -> tuple[str, str, str]:
    if explicit_model:
        return explicit_model, explicit_index, explicit_model
    try:
        mapping = json.loads(MODEL_MAP.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "uzuki.pth", "assets/indices/uzuki.index", "uzuki"
    entry = mapping.get(lang) or mapping.get("default") or {}
    return (
        entry.get("model", "uzuki.pth"),
        entry.get("index", "assets/indices/uzuki.index"),
        entry.get("name", "uzuki"),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--song", required=True, help="歌名，或者本地音频路径")
    ap.add_argument("--out", default=str(_RVC_ROOT / "out" / "sing.mp3"))
    ap.add_argument("--model", default="", help="指定模型文件；留空按语种自动选")
    ap.add_argument("--index", default="", help="配合 --model 用")
    ap.add_argument("--lang", default="auto", choices=["auto", "zh", "ja", "unknown"])
    ap.add_argument("--pitch", type=int, default=0)
    ap.add_argument("--max-seconds", type=int, default=45)
    args = ap.parse_args()

    t0 = time.time()
    lang = args.lang
    src = Path(args.song)
    if src.exists():
        audio = src
        title = src.stem
    else:
        cands = search_songs(args.song)
        audio = None
        title = args.song
        sid = None
        for c in cands:
            if not matches_title(args.song, c["name"]):
                log(f"标题对不上，跳过：{c['name']}")
                continue
            u = song_url(c["id"])
            if not u:
                u = gd_url(c["id"])
                if u:
                    log(f"官方接口要会员，改用解析源：{c['name']}（{c['id']}）")
            if not u:
                log(f"跳过拿不到音频的：{c['name']}（{c['id']}）")
                continue
            title, sid = c["name"], c["id"]
            log(f"找到：{title}（id={sid}）")
            audio = download(u, WORK / f"{sid}.mp3")
            log(f"下载完成：{audio}（{audio.stat().st_size // 1024} KB）")
            break

        if audio is None:
            log("音乐库没有合适的，去 B 站找")
            for v in bilibili_search(args.song):
                p = bilibili_download(v["bvid"], WORK / "bili")
                if p:
                    audio, title = p, v["title"]
                    log(f"B 站拿到：{title}（{v['bvid']}）")
                    break
        if audio is None:
            raise RuntimeError("音乐库和 B 站都没找到能唱的音频")

        if lang == "auto":
            lang = detect_language(sid) if sid else "unknown"
            if lang == "unknown":
                lang = guess_lang(title)

    model, index, model_name = pick_model(lang if lang != "auto" else "unknown", args.model, args.index)
    log(f"语言={lang}，唱歌模型={model_name}")

    vocals, inst = separate(audio)
    log(f"分离完成：{vocals.name} / {inst.name}")

    env = _rms_per_second(vocals) if vocals.suffix == ".wav" else []
    if env and sum(env) / len(env) < 50:
        raise RuntimeError("这首歌没分离出人声，可能是伴奏或纯音乐")

    start = best_window(vocals, args.max_seconds)
    vcut, icut = cut_pair(vocals, inst, start, args.max_seconds)
    log(f"从第 {start:.0f} 秒开始，取 {args.max_seconds} 秒")

    sung = WORK / "vocals_rvc.wav"
    convert(vcut, model, index, sung, args.pitch)
    log("换声完成")

    out = Path(args.out)
    mix(sung, icut, out)
    log(f"混音完成：{out}（{time.time()-t0:.0f} 秒）")

    print("RESULT " + json.dumps({"title": title, "lang": lang, "model": model_name,
                                  "path": str(out), "seconds": round(time.time() - t0, 1)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
