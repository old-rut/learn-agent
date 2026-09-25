from tools.file_tools import read_file,write_file,edit_file,glob_files
from tools.pwsh import pwsh


BASE_TOOLS = [pwsh, read_file, write_file, edit_file, glob_files]
BASE_HANDERS = {t.name: t for t in BASE_TOOLS}