import re

from config import WORKDIR

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


def permission_hook(tool_call)  -> str | None:
    name = tool_call["name"]
    args = tool_call["args"]
    # 闸门 1: 硬拒绝
    if name == "pwsh":
        reason = check_deny_list(args.get('command', ''))
        if reason:
            return f"\n⛔ {reason}"

    # 闸门 2 + 3: 规则匹配 → 用户审批
    reason = check_rules(name, args)
    if reason:
        decision = ask_user(name, args, reason)
        if decision == "deny":
            return "User denied"

    return None