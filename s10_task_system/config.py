# agent/config.py
from pathlib import Path

WORKDIR = Path(__file__).resolve().parent
SKILLS_DIR = WORKDIR / "skills"
PS7_PATH = r"C:\Program Files\PowerShell\7\pwsh.exe"

TRANSCRIPT_DIR = WORKDIR / ".transcripts"
TOOL_RESULTS_DIR = WORKDIR / ".tool-results"

MEMORY_DIR = WORKDIR / ".memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"

TASKS_DIR = WORKDIR / ".tasks"