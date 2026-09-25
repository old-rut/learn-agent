import os

from dotenv import load_dotenv
from langchain_core.messages import (
    AIMessage, HumanMessage, SystemMessage, ToolMessage,
)
from langchain.chat_models import init_chat_model
from hooks.trigger_hooks import trigger_hooks
from utils.execute_tool import execute_tool

from tools.tools_register import TOOLS, TOOLS_HANDERS
from config import WORKDIR

load_dotenv()

SYSTEM_PROMPT = f"""You are a coding agent at {WORKDIR}, and you are the model "DeepSeek-V4.1-Flash".
Use the provided tools to solve tasks. Act, don't explain.
You own the task end-to-end: clarify with the user, plan, decide, and execute.

Available tools:
- pwsh: run PowerShell commands
- read_file: read a text file (optional line limit)
- write_file: create or overwrite a file
- edit_file: replace first occurrence of old_text with new_text
- glob_files: find files by glob pattern (e.g. **/*.py)
- todo_writer: break a complex task into concrete, trackable steps and update their status as you go
- subagent: execute one self-contained subtask in a fresh context and return only its final result

Workflow for complex tasks:
1. Use `todo_writer` to break the task into concrete steps and track progress.
2. For each step, decide whether to do it yourself or delegate it to `subagent`.
3. Delegate when the step is self-contained and executing it would flood this
   conversation with tool output you don't need here.
4. After `subagent` returns, use `todo_writer` to mark that step complete.
5. Do the remaining steps yourself, and keep ownership of the overall task.

When to use `subagent`:
- A todo item can be fully described in one self-contained prompt.
- Executing it would flood this conversation with tool output.
- It does not depend on user feedback or on the current conversation's context.

When NOT to use `subagent`:
- The overall user request. You keep ownership; `subagent` only executes a piece.
- Steps that need clarification, user confirmation, or multiple rounds with the user.
- Simple single-step operations (one read, one command, one edit).
- Steps that depend on the current conversation or on user feedback.

The subagent cannot see our conversation and cannot ask the user questions,
so each `subagent` prompt must be fully self-contained and must specify exactly
what result to return.
"""

model = init_chat_model(
    "deepseek:deepseek-flash",
    temperature = 0.5,
    timeout = 300,
)

model_with_tools = model.bind_tools(TOOLS)

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

            output = execute_tool(tool_call, TOOLS_HANDERS)

            messages.append(ToolMessage(
                content = output,
                tool_call_id = tool_call["id"],
                name = tool_call["name"],))

            if tool_call["name"] == "todo_writer":
                used_todo = True

        rounds_since_todo = 0 if used_todo else rounds_since_todo + 1
        if rounds_since_todo >= 3:
            messages.append(HumanMessage(
                content="<reminder>Update your todos.</reminder>"
            ))
            rounds_since_todo = 0


if __name__ == "__main__":
    print(os.getcwd())
    print("s06: Subagent - fresh messages, final text returns")
    print("Enter a question, press Enter to send. Type q to quit.\n")


    history = [SystemMessage(content=SYSTEM_PROMPT)]

    while True:
        try:
            query = input("\033[36ms06 >> \033[0m")
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