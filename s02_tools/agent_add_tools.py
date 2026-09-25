import os
import glob as _glob
from pathlib import Path
from dotenv import load_dotenv
import subprocess
from langchain.tools import tool
from langchain.chat_models import init_chat_model
from langchain_core.messages import (
    AIMessage, HumanMessage, SystemMessage, ToolMessage,
)

WORKDIR = Path(os.getcwd()).resolve()


SYSTEM_PROMPT = f"""You are a coding agent at {WORKDIR},and you are the model "Deepseek-v4.1-flash".
Use the provided tools to solve tasks. Act, don't explain.

Available tools:
- pwsh: run PowerShell commands
- read_file: read a text file (optional line limit)
- write_file: create or overwrite a file
- edit_file: replace first occurrence of old_text with new_text
- glob_files: find files by glob pattern (e.g. **/*.py)
"""


load_dotenv()
api_key = os.getenv("DEEPSEEK_API_KEY")
base_url = os.getenv("DEEPSEEK_BASE_URL")
PS7_PATH = r"C:\Program Files\PowerShell\7\pwsh.exe"

def safe_path(path: str) -> Path:
    """把用户传入的路径解析到 WORKDIR 内，防止越界。"""
    p = (WORKDIR / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    if WORKDIR not in p.parents and p != WORKDIR:
        raise ValueError(f"Path escapes workspace: {path}")
    return p


@tool
def read_file(path: str, limit: int | None = None) -> str:
    """Read a UTF-8 text file. Optionally limit to the first N lines."""
    try:
        lines = safe_path(path).read_text(encoding="utf-8").splitlines()
    except Exception as e:
        return f"Error: {e}"
    if limit is not None:
        lines = lines[:limit]
    return "\n".join(lines)

@tool
def write_file(path: str, content: str) -> str:
    """Write content to a file (overwrites existing). UTF-8."""
    try:
        p = safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except Exception as e:
        return f"Error: {e}"
    return f"Wrote {len(content)} bytes to {path}"

@tool
def edit_file(path: str, old_text: str, new_text: str) -> str:
    """Replace the first occurrence of old_text with new_text in a file."""
    try:
        p = safe_path(path)
        text = p.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error: {e}"
    if old_text not in text:
        return "Error: text not found"
    try:
        p.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
    except Exception as e:
        return f"Error: {e}"
    return f"Edited {path}"

@tool
def glob_files(pattern: str) -> str:
    """Find files by glob pattern, relative to the workspace root."""
    try:
        matches = sorted(set(_glob.glob(pattern, root_dir=WORKDIR, recursive=True)))
    except Exception as e:
        return f"Error: {e}"
    shown = matches[:200]
    if len(matches) > 200:
        shown.append("... (more matches omitted; narrow the pattern)")
    return "\n".join(shown) if shown else "(no matches)"

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

TOOLS = [pwsh, read_file, write_file, edit_file, glob_files]

model_with_tools = model.bind_tools(tools=TOOLS)
TOOLS_HANDERS = {t.name: t for t in TOOLS}
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
    print("s02: Tool Use - four tools added to s01")
    print("Enter a question, press Enter to send. Type q to quit.\n")


    history = [SystemMessage(content=SYSTEM_PROMPT)]

    while True:
        try:
            query = input("\033[36ms02 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break

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