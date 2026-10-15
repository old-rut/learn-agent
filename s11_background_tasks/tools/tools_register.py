from tools.base_tools import BASE_TOOLS
from tools.subagent import subagent
from tools.task_system import run_create_task, run_update_task, run_claim_task, run_list_tasks, run_get_task, run_complete_task



# 绑定工具
task_system_tools = [run_create_task, run_update_task, run_claim_task, run_list_tasks, run_get_task, run_complete_task]
TOOLS = BASE_TOOLS + [subagent] + task_system_tools
TOOLS_HANDERS = {t.name: t for t in TOOLS}  # tool_call["name"]只是字符串，这行代码是让name与函数绑定




