import os
from dotenv import load_dotenv
import subprocess
from langchain.tools import tool
from langchain.chat_models import init_chat_model
from langchain_core.messages import (
    AIMessage, HumanMessage, SystemMessage, ToolMessage,
)

SYSTEM_PROMPT = (
    f"You are DeepSeek-V4.1-Flash, a coding agent at {os.getcwd()}. "
    f"Use pwsh to solve tasks. Act, don't explain. "
    f"Your identity is fixed: you are DeepSeek-V4.1-Flash. "
    f"If asked who you are, always answer DeepSeek-V4.1-Flash."
)

load_dotenv()
api_key = os.getenv("DEEPSEEK_API_KEY")
base_url = os.getenv("DEEPSEEK_BASE_URL")
PS7_PATH = r"C:\Program Files\PowerShell\7\pwsh.exe"

@tool
def pwsh(command: str) -> str:
    """Run a shell command. On Windows this runs in PowerShell 7."""
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(
            [PS7_PATH, "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=os.getcwd(),
            capture_output=True, text=True, errors="replace", timeout=120,
        )
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"

model = init_chat_model(
    "deepseek:deepseek-flash",
    temperature = 0.5,
    timeout = 300,
)

model_with_tools = model.bind_tools([pwsh])
TOOLS_HANDERS = {"pwsh": pwsh}
# response = model.invoke("Summarize AI trends")
# agent = create_agent(model,tools = [run_bash],system_prompt = SYSTEM_PROMPT)
# result = agent.invoke({"messages": [{"role": "user", "content": "Summarize AI trends"}]})

def agent_loop(messages):
    while True:
        response = model_with_tools.invoke(messages)

        messages.append(response)

        if not response.tool_calls:
            return

        for tool_call in response.tool_calls:
            name = tool_call["name"]
            args = tool_call["args"]
            print(f"\033[33m$ {args.get('command', '')}\033[0m")
            output = TOOLS_HANDERS[name].invoke(args)
            print(output[:200])
            messages.append(ToolMessage(
                content = output,
                tool_call_id = tool_call["id"],
                name = name,))
if __name__ == "__main__":
    print(os.getcwd())
    print("s01: Agent Loop (LangChain)")
    print("Enter a question, press Enter to send. Type q to quit.\n")


    history = [SystemMessage(content=SYSTEM_PROMPT)]

    while True:
        try:
            query = input("\033[36ms01 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break

        history.append(HumanMessage(content=query))
        agent_loop(history)

        # 打印最终回复，等价于遍历 response.content 找 text block
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