from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from langchain_core.messages import (
    HumanMessage, SystemMessage, ToolMessage,
)

from hooks.trigger_hooks import trigger_hooks
from utils.execute_tool import execute_tool
from utils.extract_text import extract_text
from tools.base_tools import BASE_TOOLS, BASE_HANDERS
from utils.build_system_prompt import SUB_SYSTEM
load_dotenv()


SUB_TOOLS= list(BASE_TOOLS)
SUB_HANDERS = dict(BASE_HANDERS)

model = init_chat_model(
    "deepseek:deepseek-flash",
    temperature = 0.5,
    timeout = 300,
)

model_with_tools = model.bind_tools(SUB_TOOLS)



@tool
def subagent(prompt: str) -> str:
    """execute one self-contained subtask in a fresh context and return only its final result"""

    print("\n\033[35m[Subagent started]\033[0m")

    messages = [SystemMessage(content=SUB_SYSTEM)]

    messages.append(HumanMessage(content=prompt))

    for _ in range(30):
        response = model_with_tools.invoke(messages)

        messages.append(response)

        if not response.tool_calls:
            force = trigger_hooks("Stop", messages)
            if force:
                messages.append(HumanMessage(content=force))
                continue
            print("\033[35m[Subagent done]\033[0m")
            print(f"\033[35m{response.content}\033[0m")
            return extract_text(response.content) or "(no summary)"

        for tool_call in response.tool_calls:
            output = execute_tool(tool_call, SUB_HANDERS)
            print(f"  \033[90m[sub] {tool_call["name"]}: {output[:100]}\033[0m")
            messages.append(ToolMessage(
                content=output,
                tool_call_id=tool_call["id"],
                name=tool_call["name"], ))

    print("\033[35m[Subagent stopped]\033[0m")
    return "Subagent stopped after 30 turns without a final answer."