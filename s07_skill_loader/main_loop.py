import os

from dotenv import load_dotenv
from langchain_core.messages import (
    AIMessage, HumanMessage, SystemMessage, ToolMessage,
)
from langchain.chat_models import init_chat_model
from hooks.trigger_hooks import trigger_hooks
from utils.execute_tool import execute_tool

from tools.tools_register import TOOLS, TOOLS_HANDERS
from utils.build_system_prompt import SYSTEM_PROMPT

load_dotenv()

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
    print("s07: Skill Loading - catalog first, full content on demand")
    print("Enter a question, press Enter to send. Type q to quit.\n")


    history = [SystemMessage(content=SYSTEM_PROMPT)]

    while True:
        try:
            query = input("\033[36ms07 >> \033[0m")
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