from config import WORKDIR


def log_hook(tool_call: dict):
    """PreToolUse: log every tool call (LangChain tool_call format)."""
    name = tool_call.get("name", "unknown")
    args = tool_call.get("args") or {}
    args_preview = str(list(args.values())[:2])[:60]
    print(f"\033[90m[HOOK] {name}({args_preview})\033[0m")
    return None

def large_output_hook(tool_call, output):
    """PostToolUse: warn on large output."""
    if len(str(output)) > 10000:
        print(f"\033[33m[HOOK] Large output from {tool_call['name']}: {len(str(output))} chars\033[0m")
    return None

# UserPromptSubmit hook: log user input before it reaches the LLM
def context_inject_hook(query: str):
    print(f"\033[90m[HOOK] UserPromptSubmit: working in {WORKDIR}\033[0m")
    return None

# Stop hook: print summary when loop is about to exit
from langchain_core.messages import AIMessage

def summary_hook(messages: list):
    tool_count = sum(
        len(m.tool_calls)
        for m in messages
        if isinstance(m, AIMessage) and m.tool_calls
    )
    print(f"\033[90m[HOOK] Stop: session used {tool_count} tool calls\033[0m")
    return None