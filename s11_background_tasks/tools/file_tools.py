from pathlib import Path
from langchain.tools import tool
import glob as _glob
from config import WORKDIR



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
