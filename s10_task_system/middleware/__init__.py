# middleware/__init__.py
"""中间件层。

**压缩只是这里的一个中间件。** 用法是**注入** —— 套在模型外面，主循环一行都不用改：

    model_with_tools = inject(model.bind_tools(TOOLS), build_compaction(model))

    # 循环里仍然是原样那句，它不知道有中间件
    response = model_with_tools.invoke(messages)

每个中间件在真正调模型**之前就地处理 messages**（写的是 `messages[:] = ...`），
所以调用方手里那个列表在执行完 `invoke` 之后**已经是处理过的**。

约定：

    middleware/<名字>/            一个中间件一个子包
    middleware/<名字>/wiring.py   一个类，实现 around(messages, call_next)

**子包不认识 `config.py`** —— 一个件不该知道自己在哪个项目里被装起来。
要喂给它的东西由这一层的 `build_xxx()` 读出来再传进去。
中间件之间**不互相 import** —— 组装是这一层唯一的职责。先注入的在外层。
"""
from config import TOOL_RESULTS_DIR, TRANSCRIPT_DIR
from middleware.compaction import Compaction


def build_compaction(model) -> Compaction:
    """组装点：把**本项目**的目录喂给压缩中间件。"""
    return Compaction(model, TRANSCRIPT_DIR, TOOL_RESULTS_DIR)


class Chain:
    """把若干中间件套在模型外面。

    只实现 `invoke` —— 够用即止，不需要伪装成一整个模型（没有 `bind_tools`、
    没有 `stream`）。要转发别的属性就显式加上去，而不是 `__getattr__` 兜底 ——
    兜底会把"这层不支持它"变成"悄悄穿透到内层"。
    """

    def __init__(self, inner, middlewares):
        self.inner = inner
        self.middlewares = list(middlewares)

    def invoke(self, messages):
        return self._call(0, messages)

    def _call(self, index, messages):
        if index >= len(self.middlewares):
            return self.inner.invoke(messages)
        return self.middlewares[index].around(
            messages, lambda msgs: self._call(index + 1, msgs))


def inject(inner, *middlewares) -> Chain:
    """把中间件包在 inner 外面。不传中间件时，行为与 inner 完全一致。"""
    return Chain(inner, middlewares)


__all__ = ["Chain", "inject", "Compaction", "build_compaction"]
