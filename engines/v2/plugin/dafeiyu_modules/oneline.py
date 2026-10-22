import re

from dafeiyu_core.module import Module, register


@register
class OnelineModule(Module):
    name = "oneline"
    description = "把回复里的换行收掉，QQ 里看到一条完整的话"

    async def on_decorating_result(self, event) -> None:
        result = event.get_result()
        chain = getattr(result, "chain", None) if result else None
        if not chain:
            return
        for comp in chain:
            text = getattr(comp, "text", None)
            if isinstance(text, str) and "\n" in text:
                comp.text = re.sub(r"\s*\n+\s*", " ", text).strip()
