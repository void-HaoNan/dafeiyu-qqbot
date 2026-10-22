class ToolSpec:
    """模块对外暴露一个 LLM 工具。"""

    def __init__(self, name: str, description: str, parameters: dict, handler):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler


class Module:
    """所有功能模块的基类。加功能只要新建一个模块文件。"""

    name = ""
    description = ""
    default_enabled = True
    priority = 100

    def __init__(self, kernel):
        self.kernel = kernel
        self.ctx = kernel
        self.cfg = kernel.cfg.module_cfg(self.name)

    async def setup(self) -> None:
        pass

    def tools(self) -> list[ToolSpec]:
        return []

    async def on_message(self, event) -> None:
        pass

    async def on_waiting_llm_request(self, event) -> None:
        pass

    async def on_llm_request(self, event, req) -> None:
        pass

    async def on_llm_response(self, event, response) -> None:
        pass

    async def on_decorating_result(self, event) -> None:
        pass


_REGISTRY: list[type[Module]] = []


def register(cls):
    _REGISTRY.append(cls)
    return cls


def all_modules() -> list[type[Module]]:
    return list(_REGISTRY)
