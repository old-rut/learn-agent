from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

from tools.file_tools import read_file,write_file,edit_file,glob_files
from tools.pwsh import pwsh
from tools.todo_writer import todo_writer
load_dotenv()



model = init_chat_model(
    "deepseek:deepseek-flash",
    temperature = 0.5,
    timeout = 300,
)


# 绑定工具
TOOLS = [pwsh, read_file, write_file, edit_file, glob_files, todo_writer]

model_with_tools = model.bind_tools(tools=TOOLS)
TOOLS_HANDERS = {t.name: t for t in TOOLS}  # tool_call["name"]只是字符串，这行代码是让name与函数绑定


