from tools.base_tools import BASE_TOOLS
from tools.subagent import subagent
from tools.todo_writer import todo_writer


# 绑定工具
TOOLS = BASE_TOOLS + [subagent] + [todo_writer]
TOOLS_HANDERS = {t.name: t for t in TOOLS}  # tool_call["name"]只是字符串，这行代码是让name与函数绑定




