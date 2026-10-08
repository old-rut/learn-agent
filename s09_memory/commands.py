# commands.py
"""命令层 —— `/` 开头的输入在这里就地处理掉，**不进模型**。

一条命令一个处理函数，签名统一为 `(args: str, messages: list) -> None`：
args 是命令名之后的那一段（可能为空串）。认不出的 `/` 也只提示、不提问 ——
错字不该变成一次真实的模型调用。

注册只有一个入口：`register()`。本层自带的命令在下面用同一个函数挂上；
处理函数依赖**运行时对象**的（比如压缩器）由组装点挂 —— 这一层不 import 中间件。
"""
import unicodedata
from collections.abc import Callable

from hooks.memory_hook.memory_commands import memories, remember

Handler = Callable[[str, list], None]

_COMMANDS: dict[str, Handler] = {}


def register(name: str, handler: Handler) -> None:
    """挂一条命令。本层自带的、组装点挂上来的，走的都是这一个口子。"""
    _COMMANDS[name] = handler


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


def run_command(query: str, messages) -> bool:
    """认得出就执行并返回 True，调用方据此跳过本轮提问。"""
    name, args = read_command(query)
    if name is None:
        return False
    handler = _COMMANDS.get(name)
    if handler is None:
        print(f"\033[33m未知命令：{name}（可用：{' '.join(sorted(_COMMANDS))}）\033[0m")
        return True
    handler(args, messages)
    return True


# 本层自带的命令。依赖运行时对象的由组装点 register() 挂进来。
register("/remember", remember)
register("/memories", memories)
