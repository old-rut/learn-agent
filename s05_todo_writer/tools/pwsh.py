from langchain.tools import tool
import os
import subprocess
from config import PS7_PATH


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