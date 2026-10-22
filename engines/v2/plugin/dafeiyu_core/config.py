import json

from . import paths

DEFAULTS = {
    "admin_qq": "",
    "bot_qq": "",
    "group": "",
    "modules": {},
}


class Config:
    """一份配置管所有模块：data/dafeiyu/config.json。"""

    def __init__(self) -> None:
        self.data: dict = {}
        self._mtime: float = 0.0
        self.load()

    def load(self) -> None:
        try:
            self._mtime = paths.CONFIG_FILE.stat().st_mtime
        except OSError:
            self._mtime = 0.0
        try:
            raw = json.loads(paths.CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        self.data = {**DEFAULTS, **raw}

    def reload_if_changed(self) -> None:
        """配置文件改了就地重载，维护模式这种开关不用重启。"""
        try:
            mtime = paths.CONFIG_FILE.stat().st_mtime
        except OSError:
            return
        if mtime != self._mtime:
            self.load()

    def save(self) -> None:
        paths.CONFIG_FILE.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def module_enabled(self, name: str, default: bool = True) -> bool:
        section = (self.data.get("modules") or {}).get(name) or {}
        return bool(section.get("enabled", default))

    def module_cfg(self, name: str) -> dict:
        return (self.data.get("modules") or {}).get(name) or {}
