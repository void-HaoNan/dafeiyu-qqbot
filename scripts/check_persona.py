#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""人格体检：扫机器人说过的话，把「出戏」的挑出来。

数据来自 logs/panel_stream.jsonl 里 dir=out 的记录（消息流插件写的）。
规则在 scripts/persona_rules.json，改规则不用改代码。

用法：
  python check_persona.py                  # 最近 50 条
  python check_persona.py --last 200       # 多看一点
  python check_persona.py --text "您好，我是AI助手"   # 单句速测
  python check_persona.py --json           # 机器可读

退出码：有 ERROR 返回 1，方便以后挂到自检里。
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(os.environ.get("QQBOT_ROOT") or Path(__file__).resolve().parents[1])
STREAM = ROOT / "logs" / "panel_stream.jsonl"
RULES = Path(__file__).resolve().parent / "persona_rules.json"

DEFAULT_RULES = {
    "max_chars_warn": 45,
    "max_chars_error": 70,
    "repeat_ratio": 0.9,
    "rules": [
        {
            "id": "ai_tone",
            "level": "error",
            "desc": "AI 腔 / 客服腔",
            "pattern": "作为(一个)?AI|我是AI|人工智能助手|有什么可以帮您|希望对您有帮助|供您参考|总之|综上所述|首先|其次|建议您|您可以",
        },
        {
            "id": "wrong_self",
            "level": "error",
            "desc": "自称不对（应该是本鱼）",
            "pattern": "本鲸|人家|本人",
        },
    ],
}
PUNCT = "。！？…～，,.!?;；:："


def load_rules() -> dict:
    if RULES.exists():
        try:
            return json.loads(RULES.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"规则文件读不了，用默认规则：{exc}", file=sys.stderr)
    return DEFAULT_RULES


def load_replies(last: int) -> list[dict]:
    if not STREAM.exists():
        return []
    rows = []
    for line in STREAM.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            r = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if r.get("dir") != "out":
            continue
        text = (r.get("text") or "").strip()
        # 表情/语音的占位记录不算发言
        if not text or text.startswith("["):
            continue
        rows.append({"ts": r.get("ts", 0), "text": text, "conv": r.get("conv_id", "")})
    return rows[-last:]


def check_one(row: dict, prev: dict | None, rules: dict, compiled: list) -> list[dict]:
    text = row["text"]
    out = []
    for rule, pattern in compiled:
        if pattern.search(text):
            out.append({
                "id": rule["id"], "level": rule.get("level", "warn"),
                "desc": rule.get("desc", ""), "text": text,
            })

    n = len(text)
    if n >= rules.get("max_chars_error", 70):
        out.append({"id": "too_long", "level": "error", "desc": f"超过 {rules['max_chars_error']} 字（{n} 字）", "text": text})
    elif n >= rules.get("max_chars_warn", 45):
        out.append({"id": "too_long", "level": "warn", "desc": f"超过 {rules['max_chars_warn']} 字（{n} 字）", "text": text})

    if "\n" in text:
        out.append({"id": "newline", "level": "error", "desc": "回复里有换行", "text": text})

    if n >= 20 and not any(ch in text for ch in PUNCT):
        out.append({"id": "no_punct", "level": "warn", "desc": "长句没有标点，像机器人念稿", "text": text})

    if re.fullmatch(r"[（(][^）)]{0,30}[）)]", text):
        out.append({"id": "action_only", "level": "warn", "desc": "整条只有括号动作", "text": text})

    if prev:
        ratio = difflib.SequenceMatcher(None, prev["text"], text).ratio()
        if ratio >= rules.get("repeat_ratio", 0.9) and text != prev["text"]:
            out.append({"id": "near_repeat", "level": "warn", "desc": f"和上一条太像（{ratio:.0%}）", "text": text})
        elif text == prev["text"]:
            out.append({"id": "repeat", "level": "error", "desc": "和上一条一模一样", "text": text})
    return out


def report(rows: list[dict], findings: list[dict], as_json: bool) -> int:
    errors = [f for f in findings if f["level"] == "error"]
    warns = [f for f in findings if f["level"] != "error"]

    if as_json:
        print(json.dumps({
            "checked": len(rows),
            "avg_chars": round(sum(len(r["text"]) for r in rows) / len(rows), 1) if rows else 0,
            "errors": len(errors),
            "warns": len(warns),
            "findings": findings,
        }, ensure_ascii=False, indent=1))
        return 1 if errors else 0

    if not rows:
        print("没有可检查的回复（panel_stream.jsonl 里还没有 out 记录）")
        return 0

    lens = [len(r["text"]) for r in rows]
    print(f"人格体检 · 检查了 {len(rows)} 条")
    print(f"平均 {sum(lens)/len(lens):.1f} 字，最长 {max(lens)} 字，带换行 {sum(1 for r in rows if chr(10) in r['text'])} 条")
    print()

    if not findings:
        print("没挑出问题。")
        return 0

    for level, group, label in (("error", errors, "ERROR"), ("warn", warns, "WARN")):
        if not group:
            continue
        counts = Counter(f["id"] for f in group)
        print(f"[{label}] 共 {len(group)} 条，{len(counts)} 类")
        for rid, cnt in counts.most_common():
            desc = next(f["desc"] for f in group if f["id"] == rid)
            print(f"  {rid} ×{cnt}：{desc}")
            example = next(f["text"] for f in group if f["id"] == rid)
            print(f"    例：{example[:60]}")
        print()
    print(f"合计：ERROR {len(errors)} 条，WARN {len(warns)} 条")
    return 1 if errors else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--last", type=int, default=50)
    ap.add_argument("--text", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rules = load_rules()
    compiled = []
    for rule in rules.get("rules", []):
        try:
            compiled.append((rule, re.compile(rule["pattern"])))
        except re.error as exc:
            print(f"规则 {rule.get('id')} 正则写错了，跳过：{exc}", file=sys.stderr)

    if args.text:
        rows = [{"ts": 0, "text": args.text.strip(), "conv": "手动"}]
    else:
        rows = load_replies(args.last)

    findings = []
    prev = None
    for row in rows:
        findings.extend(check_one(row, prev, rules, compiled))
        prev = row
    return report(rows, findings, args.json)


if __name__ == "__main__":
    sys.exit(main())
