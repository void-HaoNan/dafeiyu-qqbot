import os
from pathlib import Path


def _detect_root() -> Path:
    env = os.environ.get("QQBOT_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "AstrBot").is_dir() and (parent / "panel").is_dir():
            return parent
    for parent in here.parents:
        if (parent / "AstrBot").is_dir():
            return parent
    return here.parents[3] if len(here.parents) > 3 else here.parent


ROOT = _detect_root()
os.environ.setdefault("QQBOT_ROOT", str(ROOT))
DATA = ROOT / "data" / "dafeiyu"
LOGS = ROOT / "logs"
STICKERS = ROOT / "stickers"
PERSONAS = ROOT / "personas"
BIN = ROOT / "bin"
RVC = ROOT / "rvc"
CONFIG_FILE = DATA / "config.json"
STREAM_FILE = LOGS / "panel_stream.jsonl"
IMAGE_DIR = LOGS / "panel_images"
VOICE_DIR = LOGS / "voice"

for _d in (DATA, LOGS, IMAGE_DIR, VOICE_DIR):
    _d.mkdir(parents=True, exist_ok=True)
