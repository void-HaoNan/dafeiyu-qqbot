import subprocess

from . import paths


def ffmpeg() -> str:
    exe = paths.BIN / "ffmpeg.exe"
    return str(exe) if exe.exists() else "ffmpeg"


def to_mp3(src, dst=None):
    """wav -> mp3，QQ 语音条只认 mp3。"""
    from pathlib import Path

    src = Path(src)
    dst = Path(dst) if dst else src.with_suffix(".mp3")
    subprocess.run(
        [ffmpeg(), "-y", "-i", str(src), "-ar", "32000", "-b:a", "96k", str(dst)],
        capture_output=True,
        timeout=180,
    )
    return dst if dst.exists() else None


def normalize(src, target_db: float = -2.0):
    """峰值归一化，loudnorm 对低电平无效，这里用 volumedetect 算增益。"""
    import re
    from pathlib import Path

    src = Path(src)
    probe = subprocess.run(
        [ffmpeg(), "-i", str(src), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    match = re.search(r"max_volume:\s*(-?[\d.]+) dB", probe.stderr or "")
    if not match:
        return src
    gain = min(target_db - float(match.group(1)), 30.0)
    if gain <= 1.0:
        return src
    out = src.with_name(src.stem + "_norm" + src.suffix)
    subprocess.run(
        [ffmpeg(), "-y", "-i", str(src), "-af", f"volume={gain:.1f}dB,alimiter=limit=0.95", str(out)],
        capture_output=True,
        timeout=120,
    )
    return out if out.exists() else src
