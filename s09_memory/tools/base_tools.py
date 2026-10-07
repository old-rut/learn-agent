from tools.file_tools import read_file,write_file,edit_file,glob_files
from tools.pwsh import pwsh
from tools.skill_loader import load_skill

BASE_TOOLS = [pwsh, read_file, write_file, edit_file, glob_files, load_skill]
BASE_HANDERS = {t.name: t for t in BASE_TOOLS}