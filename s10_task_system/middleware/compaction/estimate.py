# middleware/compaction/estimate.py
"""计量 —— 全文唯一的 token 单位。

所有阈值、检查、日志都用这里的函数，不允许"字符数"和"token 数"混用。
"""
import json

from middleware.compaction.tokenizer import get_token

# chat 模板 + 角色标记。实测是**每次请求**的常量，不是每条消息的
# （2/3/5/10/20 条消息上分别是 30/31/33/30/30）。
REQUEST_OVERHEAD = 30


def _message_text(m) -> str:
    """一条消息里真正要送出去、因而占 token 的全部文本。

    **不只是 content**：`tool_calls` 的 name/args 也发给模型；`additional_kwargs`
    里有 DeepSeek 思考模式的 `reasoning_content`，它必须回传（不回传 API 直接 400）。
    """
    parts = []
    content = m.content
    if isinstance(content, str):
        parts.append(content)
    elif content is not None:
        parts.append(json.dumps(content, ensure_ascii=False, default=str))

    tool_calls = getattr(m, "tool_calls", None)
    if tool_calls:
        parts.append(json.dumps(tool_calls, ensure_ascii=False, default=str))

    extra = getattr(m, "additional_kwargs", None)
    if extra:
        parts.append(json.dumps(extra, ensure_ascii=False, default=str))

    return "\n".join(p for p in parts if p)


def estimate_tokens(messages) -> int:
    """这批消息占多少 token。

    **不含工具 schema**：绑 8 个工具实测固定占 861 token，那部分挂在 `bind_tools`
    上、不在 messages 里。它是常量，对"什么时候该压"只是把预算整体平移一点。
    """
    return sum(get_token(_message_text(m)) for m in messages) + REQUEST_OVERHEAD
