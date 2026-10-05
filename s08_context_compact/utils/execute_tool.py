from hooks.trigger_hooks import trigger_hooks


def execute_tool(tool_call, handers) -> str:
    name = tool_call["name"]
    args = tool_call["args"]
    print(f"\033[33m$ {args.get('command', '')}\033[0m")
    tool_called = trigger_hooks("PreToolUse", tool_call)
    if tool_called:
        return tool_called
    try:
        output = handers[name].invoke(args) if name in handers else f"Unknown tool: {name}"
    except Exception as e:
        output = f"Error: {e}"
    trigger_hooks("PostToolUse", tool_call, output)
    return output