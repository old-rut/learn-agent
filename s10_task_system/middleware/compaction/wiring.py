# middleware/compaction/wiring.py
"""接线 —— 压缩中间件。

按中间件层的约定实现 `around(messages, call_next)`：middleware/__init__.py 的
inject() 把它套在模型外面，主循环照旧写 `model_with_tools.invoke(messages)`。

它只管两件**周边**的事：谁在什么时候调它、调崩了怎么办。
压缩算法本身全在 context_compact.py，命令怎么认在顶层 commands.py。
"""
from langchain_core.messages import HumanMessage

from middleware.compaction.context_compact import (
    MAX_REACTIVE_RETRIES, ContextCompactor, is_injected,
)

# 反应式触发只在**真的**"上下文超限"时才动手，而不是吞掉所有异常。
# 匹配不上就原样抛出 —— 宁可炸得清楚，也不要压着一堆看不出原因的失败往下跑。
OVERFLOW_HINTS = (
    "context length", "context_length", "maximum context",
    "prompt too long", "too many tokens", "reduce the length",
)

def looks_like_overflow(err: Exception) -> bool:
    text = str(err).lower()
    return any(hint in text for hint in OVERFLOW_HINTS)


def active_request_text(messages) -> str:
    """当前用户请求 = 最后一条**非注入**的 HumanMessage 原文（摘要的框架）。"""
    for m in reversed(messages):
        if (isinstance(m, HumanMessage) and isinstance(m.content, str)
                and not is_injected(m)):
            return m.content
    return "(not supplied)"


class Compaction:
    """压缩中间件。两个目录由外层传进来 —— 它不认识 `config.py`。"""

    def __init__(self, model, transcript_dir, tool_results_dir):
        self.compactor = ContextCompactor(model, transcript_dir, tool_results_dir)

    # ── 中间件协议 ────────────────────────────────────────────

    def around(self, messages, call_next):
        """每次调模型前压一次；API 报超限则反应式重试一次。

        **就地改 `messages`**（`messages[:] = ...`）—— 这是"主循环一行不用改"的前提。
        写成 `messages = prepare(...)` 会让调用方的历史悄悄脱钩，而且不报错。
        """
        retries = 0
        while True:
            messages[:] = self.compactor.prepare(
                messages, active_request_text(messages))
            try:
                return call_next(messages)
            except Exception as e:
                if retries >= MAX_REACTIVE_RETRIES or not looks_like_overflow(e):
                    raise                       # 不是超限、或已经救过一次 → 交出去
                retries += 1
                print(f"\033[33m[compact] reactive after: {e}\033[0m")
                messages[:] = self.compactor.reactive_compact(
                    messages, active_request_text(messages))

    # ── 手动触发（不走模型调用路径，所以不在 around 里）────────────

    def compact_now(self, focus: str, messages) -> None:
        """手动压一次。命令层已经把 `/compact` 后面的参数剥好了。

        切片赋值 —— 写成 `messages = prepare(...)` 会让调用方的历史悄悄脱钩。
        """
        messages[:] = self.compactor.prepare(
            messages, active_request_text(messages), focus=focus or None, force=True)
