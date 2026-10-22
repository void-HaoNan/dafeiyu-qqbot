"""不依赖 AstrBot 的冒烟测试：语法、模块契约、配置样例。

用法：
    python tests/smoke.py
"""

import ast
import json
import pathlib
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "engines" / "v2" / "plugin"
MODULES = PLUGIN / "dafeiyu_modules"
problems: list[str] = []


def check_py(path: pathlib.Path) -> None:
    try:
        ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        problems.append(f"{path.relative_to(ROOT)}: {exc}")


for py in PLUGIN.rglob("*.py"):
    check_py(py)
for py in (ROOT / "scripts").glob("*.py"):
    check_py(py)
for py in (ROOT / "tests").glob("*.py"):
    check_py(py)

# 每个模块文件要有 @register 和 name
for py in MODULES.glob("*.py"):
    if py.name.startswith("_"):
        continue
    text = py.read_text(encoding="utf-8")
    if "@register" not in text or "name =" not in text:
        problems.append(f"{py.name}: 缺少 @register 或 name")

# metadata
meta = PLUGIN / "metadata.yaml"
if not meta.exists() or "name: astrbot_plugin_dafeiyu" not in meta.read_text(encoding="utf-8"):
    problems.append("metadata.yaml 缺 name")

# 配置样例
for name in ("dafeiyu.config.example.json",):
    p = ROOT / "config-examples" / name
    try:
        json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        problems.append(f"config-examples/{name}: {exc}")

# 必需文件
for rel in ("README.md", "INSTALL.md", "ARCHITECTURE.md", "docs/CONFIG.md", "patches/README.md"):
    if not (ROOT / rel).exists():
        problems.append(f"缺文件：{rel}")

print(f"模块数：{len(list(MODULES.glob('*.py'))) - 1}")
if problems:
    print("发现问题：")
    for item in problems:
        print(" -", item)
    sys.exit(1)
print("smoke OK")
