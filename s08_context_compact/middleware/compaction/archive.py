# middleware/compaction/archive.py
"""存储 —— 两个目录，职责完全不同，互不重复。

    tool_results/<tool_call_id>.txt    被指针化/预览的工具结果**原文**，模型按指针 read_file
    transcripts/session_<id>.jsonl     离开上下文的**全部**消息，原样，人/工具恢复用

唯一规则：**归档 = 离开上下文的消息，原样写入。不解析、不跳过、不改编。**
"""
import json
import re
import uuid
from pathlib import Path

from langchain_core.messages import messages_to_dict

from middleware.compaction.tokenizer import get_token, truncate_to_tokens


# 压缩器注入的那条消息，靠开头的 [Compacted] 认出来。
# 代码读**前缀**来认类型，但从不从标记里取路径 —— 路径记在实例字段上。
ARCHIVE_MARKER_RE = re.compile(r"^\[Compacted\]")

# 幂等守卫：内容是否已经是"指向别处的引用"。
POINTER_RE = re.compile(r"^\[tool result archived at .+\]$")
PERSISTED_PREFIX = "<persisted-output>"


def is_already_derived(content: str) -> bool:
    """内容是否已经是指针或预览 wrapper —— 不能对引用再引用。

    长度守卫**碰巧**也能挡住它，但那是长度巧合不是设计保证，
    一旦调整比例防线就没了。所以立成显式规则。
    """
    if not isinstance(content, str):
        return False
    return bool(POINTER_RE.match(content)) or content.startswith(PERSISTED_PREFIX)


def _safe_name(tool_call_id: str) -> str:
    """tool_call_id 进文件名 —— 只留字面上安全的字符。"""
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(tool_call_id)) or "unknown"


class Archive:
    """一次会话一份归档 + 一个工具结果目录。

    路径记在**实例字段**上，不解析标记：摘要会把标记整个换掉，
    而且标记里那个路径是"给模型看的字符串"。
    """

    def __init__(self, transcript_dir, tool_results_dir):
        self.transcript_dir = Path(transcript_dir)
        self.tool_results_dir = Path(tool_results_dir)
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        self.tool_results_dir.mkdir(parents=True, exist_ok=True)

        self.session_id = uuid.uuid4().hex[:8]
        self.transcript_path = self.transcript_dir / f"session_{self.session_id}.jsonl"

    # ── 冷存储：离开上下文的消息 ────────────────────────────────

    def write_messages(self, messages) -> int:
        """把消息原样追加进归档，返回写入条数。

        追加模式，同一会话只有一份文件（I4）。归档里会出现自引用行 ——
        上一次的标记被当成普通消息写进来，这是"原样写入"的必然结果。
        """
        if not messages:
            return 0
        with self.transcript_path.open("a", encoding="utf-8") as f:
            for record in messages_to_dict(messages):
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return len(messages)

    @property
    def transcript_note(self) -> str:
        """摘要消息末行：给读者一个绝对路径，按需回读。"""
        return f"Full transcript: {self.transcript_path.resolve()}"

    def marker_text(self, pre_tokens: int, post_tokens: int, archived_count: int) -> str:
        """摘要消息首行。数字全部由代码算 —— 不让模型复述它算不出来的东西。"""
        return (
            f"[Compacted]  est. {pre_tokens:,} -> {post_tokens:,} tok"
            f" · {archived_count} messages archived"
            f" · transcript: {self.transcript_path.name}"
        )

    # ── 热存储：工具结果原文 ────────────────────────────────────

    def persist_tool_result(self, tool_call_id: str, content: str) -> Path | None:
        """把工具结果原文写进 tool_results/，返回路径。

        内容已经是指针/预览时返回 None —— **调用方必须据此跳过**。
        这是 I3 的落点：任何替换动作之前，原文必须已经在磁盘上。
        """
        if is_already_derived(content):
            return None
        path = self.tool_results_dir / f"{_safe_name(tool_call_id)}.txt"
        path.write_text(content, encoding="utf-8")
        return path

    def persisted_output_path(self, tool_call_id: str) -> Path | None:
        """已落盘的工具结果路径；文件不在了返回 None。"""
        path = self.tool_results_dir / f"{_safe_name(tool_call_id)}.txt"
        return path if path.exists() else None

    # ── 两种替换文案 ────────────────────────────────────────────

    def pointer_text(self, path: Path) -> str:
        """指针：只留路径。用于 middle（损失大）。"""
        return f"[tool result archived at {path}]"

    def preview_text(self, path: Path, content: str, preview_tokens: int) -> str:
        """预览：原文前 N 个 **token** + 路径。用于 recent（损失小）。

        必须以 PERSISTED_PREFIX 开头 —— is_already_derived 靠它判幂等。
        """
        head = truncate_to_tokens(content, preview_tokens)
        return (
            f"{PERSISTED_PREFIX}\n"
            f"[preview: first {get_token(head)} of {get_token(content)} tok"
            f" · full text at {path}]\n"
            f"{head}\n"
            f"</persisted-output>"
        )
