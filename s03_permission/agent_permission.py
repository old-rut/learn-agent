import os
import re
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
    # 工具内部再挡一道：即使调用方绕过了权限门控，硬拒绝名单依然生效
    blocked = check_deny_list(command)
    if blocked:
        return f"Error: {blocked}"
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
TOOLS_HANDERS = {t.name: t for t in TOOLS}

# =============================  权限门控  =============================

# 闸门1：硬拒绝 —— 命中直接拦下，不给用户审批的机会
# 注意两点：
#   1. PowerShell 命令名不区分大小写，所以统一转小写后再做子串匹配；
#   2. rm / del / erase / rd / ri / rmdir 都是 Remove-Item 的别名。
#      别名不写进列表的话，一句 `ri -Recurse -Force C:\` 就能绕过全部检查。
DENY_LIST = [
    # 磁盘 / 分区级破坏，数据不可恢复
    "format-volume", "format.com", "format c:", "clear-disk",
    "initialize-disk", "remove-partition", "diskpart",
    # 引导配置 / 卷影副本 / 系统备份
    "bcdedit", "wbadmin delete", "vssadmin delete", "delete shadows",
    # 注册表根键整体删除
    "remove-item hklm:", "remove-item hkey_local_machine",
    "reg delete hklm", "reg delete hkey_local_machine",
    # 关闭安全防护（Defender / 防火墙）
    "set-mppreference -disablerealtimemonitoring",
    "add-mppreference -exclusionpath",
    "set-netfirewallprofile -enabled false",
    # 关机 / 重启 / 注销（Windows 上 shutdown 就是 shutdown.exe）
    "stop-computer", "restart-computer", "logoff",
    "shutdown /s", "shutdown /r", "shutdown /p", "shutdown -s",
]

# 上面是「一眼可读」的字面量匹配；下面这几种要几个词凑齐才危险、
# 或者参数顺序能随便换（比如 -Verb RunAs 可以放最后），只能用正则兜底。
# 每项是 (说明, 正则)，说明会出现在拦截提示里
DENY_PATTERNS = [
    # 递归删除盘符根目录 / Windows / 系统关键目录 / C:\Users 整体 / 注册表根键
    # 命中示例：Remove-Item -Recurse -Force C:\
    #           ri -Recurse C:\Windows\System32
    #           rm -r -fo C:\*
    ("recursive delete of a system location", re.compile(
        r"(?i)\b(?:remove-item|rm|del|erase|rd|ri|rmdir)\b"
        r"(?=[^\n|;]*\s-(?:recurse|r|fo|force)\b)"     # 必须带递归/强制参数（-r、-fo 是别名）
        r"[^\n|;]*?(?:"
        r"[a-z]:[\\*]{0,2}(?=\s|$|[;|&\"'])"           # 盘符根，如 C:\ 或 C:\*
        r"|c:\\windows(?:\\\*)?(?=\s|$|[;|&\"'])"      # C:\Windows 本身
        r"|c:\\windows\\(?:system32|syswow64|fonts)\b"  # 系统关键目录
        r"|c:\\users(?:\\\*?)?(?=\s|$|[;|&\"'])"       # C:\Users 整体
        r"|\$env:(?:systemdrive|systemroot|windir)(?=[\\\s*]|$)"
        r"|hk(?:lm|ey_local_machine):"
        r")"
    )),
    # UAC 提权：Start-Process -Verb RunAs（start / saps 都是别名，参数顺序任意）
    ("UAC elevation via -Verb RunAs",
     re.compile(r"(?i)\b(?:start-process|saps|start)\b[^\n|;]*\s-verb\s+runas\b")),
    # Windows 11 自带的 sudo.exe
    ("privilege escalation via sudo",
     re.compile(r"(?i)(?:^|[;&|]\s*)sudo(?:\.exe)?\s")),
]


def check_deny_list(command: str) -> str | None:
    """闸门1：硬拒绝。返回拦截原因，None 表示放行。"""
    low = command.lower()          # PowerShell 不区分大小写
    for keyword in DENY_LIST:
        if keyword in low:
            return f"'{keyword}' is on the deny list"
    for label, pattern in DENY_PATTERNS:
        if pattern.search(command):
            return f"hard-denied: {label}"
    return None


# 闸门2：可疑但不直接拒绝 —— 正常任务也可能用到，交给用户审批
DANGEROUS_PATTERNS = [
    # 删除文件 / 清空内容（rm、del、erase、rd、ri、rmdir 都是别名）
    re.compile(r"(?i)\b(?:remove-item|rm|del|erase|rd|ri|rmdir|clear-content|clc)\b"),
    # 账号 / ACL 权限（提权、留后门）
    re.compile(
        r"(?i)\b(?:new-localuser|set-localuser|remove-localuser|add-localgroupmember"
        r"|net\s+(?:user|localgroup)|takeown|icacls|set-acl)\b"
    ),
    # 服务 / 计划任务 / 启动项（持久化）
    re.compile(
        r"(?i)\b(?:stop-service|set-service|new-service|sc\.exe|schtasks"
        r"|register-scheduledtask|new-scheduledtask)\b"
    ),
    # 注册表写入
    re.compile(
        r"(?i)\b(?:set-itemproperty|new-itemproperty|remove-itemproperty"
        r"|reg\s+(?:add|delete|import))\b"
    ),
    # 下载即执行 / 动态执行（iwr、irm、curl、wget 都是别名）
    re.compile(
        r"(?i)\b(?:invoke-expression|iex|invoke-webrequest|iwr"
        r"|invoke-restmethod|irm|curl|wget)\b"
    ),
    # 执行策略 / 防火墙规则
    re.compile(
        r"(?i)\b(?:set-executionpolicy|new-netfirewallrule"
        r"|remove-netfirewallrule|set-netfirewallprofile)\b"
    ),
    # 强杀进程
    re.compile(r"(?i)\b(?:stop-process|spps|taskkill)\b"),
]


def contains_destructive_command(command: str) -> bool:
    """检查命令中是否包含危险模式"""
    return any(pattern.search(command) for pattern in DANGEROUS_PATTERNS)

PERMISSION_RULES = [
    {
        "tools": ["read_file", "write_file", "edit_file"],
        "check": lambda args: not (WORKDIR / args.get("path", "")).resolve().is_relative_to(WORKDIR),
        "message": "Access outside workspace",
    },
    {
        "tools": ["pwsh"],
        "check": lambda args: contains_destructive_command(args.get("command", "")),
        "message": "Potentially destructive command",
    },
]

def check_rules(tool_name: str, args: dict) -> str | None:
    for rule in PERMISSION_RULES:
        if tool_name in rule["tools"] and rule["check"](args):
            return rule["message"]
    return None


# 闸门3
def ask_user(tool_name: str, args: dict, reason: str) -> str:
    print(f"\n⚠  {reason}")
    print(f"   Tool: {tool_name}({args})")
    choice = input("   Allow? [y/N] ").strip().lower()
    return "allow" if choice in ("y", "yes") else "deny"


def check_permission(tool_call) -> bool:
    name = tool_call["name"]
    args = tool_call["args"]
    # 闸门 1: 硬拒绝
    if name == "pwsh":
        reason = check_deny_list(args.get('command', ''))
        if reason:
            print(f"\n⛔ {reason}")
            return False

    # 闸门 2 + 3: 规则匹配 → 用户审批
    reason = check_rules(name, args)
    if reason:
        decision = ask_user(name, args, reason)
        if decision == "deny":
            return False

    return True

# =============================  主循环  =============================
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
            if not check_permission(tool_call):  # ← 新增
                messages.append(ToolMessage(
                    content="Permission denied",
                    tool_call_id=tool_call["id"],
                    name=name,
                ))
                continue
            output = TOOLS_HANDERS[name].invoke(args)
            print(output[:200])
            messages.append(ToolMessage(
                content = output,
                tool_call_id = tool_call["id"],
                name = name,))




if __name__ == "__main__":
    print(os.getcwd())
    print("s03: Permission_check")
    print("Enter a question, press Enter to send. Type q to quit.\n")


    history = [SystemMessage(content=SYSTEM_PROMPT)]

    while True:
        try:
            query = input("\033[36ms03 >> \033[0m")
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