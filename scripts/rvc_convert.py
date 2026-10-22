#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用 RVC 把一段人声换成指定音色。

必须用 RVC 整合包自带的 python 跑（torch 版本和依赖都在里面）：
  <RVC 整合包>\\runtime\\python.exe scripts\\rvc_convert.py ...

例子：
  ... rvc_convert.py --input a.wav --output b.wav --model guanguanV1.pth \
      --index assets/indices/guanguanV1.index

参数说明：pitch 是升降调（男转女一般 +12，女转女 0），index-rate 越高越像，
protect 保护气声（0.33 左右）。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("QQBOT_ROOT") or Path(__file__).resolve().parents[1])
RVC_ROOT = Path(os.environ.get("RVC_ROOT") or ROOT / "rvc")
RVC_DIR = Path(os.environ.get("RVC_PKG") or "")
if not RVC_DIR.is_dir():
    _hits = sorted(RVC_ROOT.glob("pkg/*/runtime/python.exe"))
    RVC_DIR = _hits[0].parent.parent if _hits else RVC_ROOT


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--model", required=True, help="assets/weights 下的文件名，比如 guanguanV1.pth")
    ap.add_argument("--index", default="", help="索引文件路径，留空则不用")
    ap.add_argument("--pitch", type=int, default=0, help="升降半音")
    ap.add_argument("--f0-method", default="rmvpe", choices=["rmvpe", "fcpe", "pm", "harvest", "crepe"])
    ap.add_argument("--index-rate", type=float, default=0.6)
    ap.add_argument("--protect", type=float, default=0.33)
    ap.add_argument("--device", default="")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    # 先把我们自己的参数收走，再让 RVC 的 Config 去解析（它也会读 sys.argv）
    sys.argv = [sys.argv[0]]

    os.chdir(RVC_DIR)
    sys.path.insert(0, str(RVC_DIR))
    os.environ.setdefault("weight_root", "assets/weights")
    os.environ.setdefault("index_root", "logs")
    os.environ.setdefault("outside_index_root", "assets/indices")
    os.environ.setdefault("rmvpe_root", "assets/rmvpe")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

    import soundfile as sf

    from configs.config import Config
    from infer.vc.modules import VC

    cfg = Config()
    if args.device:
        cfg.device = args.device
        cfg.is_half = args.device.startswith("cuda")

    t0 = time.time()
    vc = VC(cfg)
    vc.get_vc(args.model)
    info, (sr, audio) = vc.vc_single(
        0,
        str(Path(args.input).resolve()),
        args.pitch,
        args.f0_method,
        str(Path(args.index).resolve()) if args.index else "",
        args.index_rate,
        0,
        0.5,
        args.protect,
    )
    if audio is None:
        print(info)
        return 1
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), audio, sr)
    print(f"ok {out} {sr}Hz {len(audio)/sr:.2f}s 用时 {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
