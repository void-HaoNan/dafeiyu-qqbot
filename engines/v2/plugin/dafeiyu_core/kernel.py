import importlib
import pkgutil
import traceback

from astrbot.api import logger

from . import config, onebot, paths, stream
from .module import Module, all_modules


class Kernel:
    """插件内核：加载配置、发现模块、分发钩子、注册工具。"""

    def __init__(self, plugin, context) -> None:
        self.plugin = plugin
        self.context = context
        self.cfg = config.Config()
        self.bot = onebot.OneBot()
        self.modules: list[Module] = []

    async def setup(self) -> None:
        import dafeiyu_modules

        for info in pkgutil.iter_modules(dafeiyu_modules.__path__):
            if info.name.startswith("_"):
                continue
            importlib.import_module(f"dafeiyu_modules.{info.name}")

        for cls in sorted(all_modules(), key=lambda c: getattr(c, "priority", 100)):
            if not self.cfg.module_enabled(cls.name, cls.default_enabled):
                logger.info(f"[dafeiyu] 模块关闭：{cls.name}")
                continue
            try:
                module = cls(self)
                await module.setup()
                self.modules.append(module)
                self._register_tools(module)
                logger.info(f"[dafeiyu] 模块加载：{cls.name}")
            except Exception:  # noqa: BLE001
                logger.error(f"[dafeiyu] 模块 {cls.name} 加载失败\n{traceback.format_exc()}")

    def _register_tools(self, module: Module) -> None:
        specs = module.tools() or []
        if not specs:
            return
        try:
            from astrbot.core.agent.tool import FunctionTool

            tools = []
            for spec in specs:
                async def handler(event, _spec=spec, **kwargs):
                    return await _spec.handler(event, **kwargs)

                tools.append(
                    FunctionTool(
                        name=spec.name,
                        description=spec.description,
                        parameters=spec.parameters,
                        handler=handler,
                    )
                )
            self.context.add_llm_tools(*tools)
        except Exception:  # noqa: BLE001
            logger.error(f"[dafeiyu] 工具注册失败 {module.name}\n{traceback.format_exc()}")

    async def dispatch(self, hook: str, *args) -> None:
        for module in self.modules:
            fn = getattr(module, hook, None)
            if not fn:
                continue
            try:
                await fn(*args)
            except Exception:  # noqa: BLE001
                logger.error(f"[dafeiyu] {module.name}.{hook} 出错\n{traceback.format_exc()}")

    def status(self) -> dict:
        return {
            "root": str(paths.ROOT),
            "modules": [
                {"name": m.name, "description": m.description} for m in self.modules
            ],
        }
