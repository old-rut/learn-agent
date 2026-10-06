# middleware/compaction/wiring.py
"""接线 —— 压缩中间件。

按中间件层的约定实现 `around(messages, call_next)`：middleware/__init__.py 的
inject() 把它套在模型外面，主循环照旧写 `model_with_tools.invoke(messages)`。

它只管三件**周边**的事：谁在什么时候调它、调崩了怎么办、手动命令怎么认。
压缩算法本身全在 context_compact.py。
"""
import unicodedata

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

COMPACT_COMMAND = "/compact"


def looks_like_overflow(err: Exception) -> bool:
    text = str(err).lower()
    return any(hint in text for hint in OVERFLOW_HINTS)


def read_command(query: str) -> tuple[str | None, str]:
    """把一行输入拆成 (命令名, 参数)；不是命令就返回 (None, "")。

    NFKC 归一化：中文输入法打出的全角 `／` 和 `/` 看着一样、字节不同。
    命令名按**整词**匹配 —— `/compactx` 不算 `/compact`。
    """
    text = unicodedata.normalize("NFKC", query).strip()
    if not text.startswith("/"):
        return None, ""
    parts = text.split(maxsplit=1)
    return parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")


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

    def handle_command(self, query: str, messages) -> bool:
        """处理 `/compact [focus]`。**吃掉了这行输入就返回 True**，调用方据此跳过本轮提问。

        认不出的 `/` 命令也只提示、不进模型 —— 错字不该变成一次真实的模型调用。
        """
        name, focus = read_command(query)
        if name is None:
            return False                                # 不是命令，照常提问

        if name != COMPACT_COMMAND:
            print(f"\033[33m未知命令：{name}（只有 {COMPACT_COMMAND} [focus]）\033[0m")
            return True                                 # 吃掉了，不进模型

        messages[:] = self.compactor.prepare(           # 切片赋值
            messages, active_request_text(messages), focus=focus or None, force=True)
        return True
