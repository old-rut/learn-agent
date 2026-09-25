import os
from langchain_core.messages import (
    AIMessage, HumanMessage, SystemMessage, ToolMessage,
)
from tools.tools_register import model_with_tools,TOOLS_HANDERS
from hooks.trigger_hooks import trigger_hooks

from config import WORKDIR

SYSTEM_PROMPT = f"""You are a coding agent at {WORKDIR},and you are the model "Deepseek-v4.1-flash".
Use the provided tools to solve tasks. Act, don't explain.

Available tools:
- pwsh: run PowerShell commands
- read_file: read a text file (optional line limit)
- write_file: create or overwrite a file
- edit_file: replace first occurrence of old_text with new_text
- glob_files: find files by glob pattern (e.g. **/*.py)
- todo_writer: create and manage a task list for your current coding session
"""



# =============================  主循环  =============================
def agent_loop(messages):
    rounds_since_todo = 0
    while True:
        response = model_with_tools.invoke(messages)

        messages.append(response)

        if not response.tool_calls:
            force = trigger_hooks("Stop", messages)
            if force:
                messages.append(HumanMessage(content=force))
                continue
            return

        used_todo = False
        for tool_call in response.tool_calls:
            name = tool_call["name"]
            args = tool_call["args"]
            print(f"\033[33m$ {args.get('command', '')}\033[0m")
            tool_called = trigger_hooks("PreToolUse", tool_call)
            if tool_called:
                messages.append(ToolMessage(content=tool_called,
                                            name=name,
                                            tool_call_id=tool_call["id"]))
                continue
            try:
                output = TOOLS_HANDERS[name].invoke(args) if name in TOOLS_HANDERS else f"Unknown tool: {name}"
            except Exception as e:
                output = f"Error: {e}"
            trigger_hooks("PostToolUse", tool_call, output)

            if tool_call["name"] == "todo_writer":
                used_todo = True

            messages.append(ToolMessage(
                content = output,
                tool_call_id = tool_call["id"],
                name = name,))

        rounds_since_todo = 0 if used_todo else rounds_since_todo + 1
        if rounds_since_todo >= 3:
            messages.append(HumanMessage(
                content="<reminder>Update your todos.</reminder>"
            ))
            rounds_since_todo = 0


if __name__ == "__main__":
    print(os.getcwd())
    print("s05: TodoWrite - plan before execution")
    print("Enter a question, press Enter to send. Type q to quit.\n")


    history = [SystemMessage(content=SYSTEM_PROMPT)]

    while True:
        try:
            query = input("\033[36ms05 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", "bye", ""):
            break
        trigger_hooks("UserPromptSubmit", query)
        history.append(HumanMessage(content=query))
        agent_loop(history)

        # 打印最终回复，等价于遍历 response.content 找 text block
        print("="*50)
        final = history[-1]
        if isinstance(final, AIMessage):
            if isinstance(final.content, str):
                print(final.content)
            elif isinstance(final.content, list):
                for block in final.content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        print(block["text"])
                    elif getattr(block, "type", None) == "text":
                        print(block.text)

        print()