from hooks.permission_hook import permission_hook
from hooks.other_hooks import *
from hooks.memory_hook.recall_memory import load_memories
from hooks.memory_hook.extract_memory import store_memories

HOOKS = {"UserPromptSubmit": [], "PreToolUse": [], "PostToolUse": [], "Stop": [], "PreAgent": [], "PostAgent": []}

def register_hook(event: str, callback):
    HOOKS[event].append(callback)

def trigger_hooks(event: str, *args):
    for callback in HOOKS[event]:
        result = callback(*args)
        if result is not None:  # A hook result blocks this tool call.
            return result
    return None


# ==========================================================================
register_hook("UserPromptSubmit", context_inject_hook)
register_hook("PreToolUse", permission_hook)
register_hook("PreToolUse", log_hook)
register_hook("PostToolUse", large_output_hook)
register_hook("Stop", summary_hook)
register_hook("PreAgent", load_memories)
register_hook("PostAgent", store_memories)