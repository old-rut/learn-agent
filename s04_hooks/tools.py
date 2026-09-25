from pathlib import Path
import os
import subprocess

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain.tools import tool
import glob as _glob
from config import WORKDIR,PS7_PATH

load_dotenv()



# =============================  定义工具  =============================

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


# 绑定工具
TOOLS = [pwsh, read_file, write_file, edit_file, glob_files]

model_with_tools = model.bind_tools(tools=TOOLS)
TOOLS_HANDERS = {t.name: t for t in TOOLS}  # tool_call["name"]只是字符串，这行代码是让name与函数绑定


