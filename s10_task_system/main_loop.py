import os

from dotenv import load_dotenv
from langchain_core.messages import (
    AIMessage, HumanMessage, SystemMessage, ToolMessage,
)
from langchain.chat_models import init_chat_model
from commands import register, run_command
from hooks.trigger_hooks import trigger_hooks
from utils.execute_tool import execute_tool

from tools.tools_register import TOOLS, TOOLS_HANDERS
from utils.build_system_prompt import SYSTEM_PROMPT

from middleware import build_compaction, inject
from utils.stream_print import stream_print

load_dotenv()

model = init_chat_model(
    "deepseek:deepseek-flash",
    temperature = 0.5,
    timeout = 300,
)

# 压缩是**注入**进来的，不写在循环里：套在模型外面，
# 每次 invoke 前就地处理 messages，循环对此一无所知。
COMPACTION = build_compaction(model)
model_with_tools = inject(model.bind_tools(TOOLS), COMPACTION)

# 命令注册。压缩器是运行时对象，命令层不认识它，所以在这一层挂上去 ——
# 跟上面建它的是同一个组装点。
register("/compact", COMPACTION.compact_now)


# =============================  主循环  =============================
def agent_loop(messages):
    rounds_since_todo = 0
    trigger_hooks("PreAgent", messages)
    while True:
        response = model_with_tools.invoke(messages)

        messages.append(response)

        if not response.tool_calls:
            force = trigger_hooks("Stop", messages)
            if force:
                messages.append(HumanMessage(content=force))
                continue
            trigger_hooks("PostAgent", messages)
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
    print("s09: Memory - selective knowledge across sessions")
    print("Enter a question, press Enter to send. Type q to quit.")
    print("Type /remember <text> to save a memory, /memories to list them.\n")


    history = [SystemMessage(content=SYSTEM_PROMPT)]

    while True:
        try:
            query = input("\033[36ms09 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", "bye", ""):
            break
        if run_command(query, history):
            continue
        trigger_hooks("UserPromptSubmit", query)
        history.append(HumanMessage(content=query))
        agent_loop(history)

        # 打印最终回复，等价于遍历 response.content 找 text block
        print("="*50)
        final = history[-1]
        if isinstance(final, AIMessage):
            if isinstance(final.content, str):
                stream_print(final.content)
            elif isinstance(final.content, list):
                for block in final.content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        stream_print(block["text"])
                    elif getattr(block, "type", None) == "text":
                        stream_print(block.text)

        print()
