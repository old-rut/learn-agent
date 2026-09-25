# s03: Permission — 三层门控，把「能执行」和「该执行」分开

[s01](../s01_loop/) → [s02](../s02_tools/) → `s03` → [s04](../s04_hooks/) → [s05](../s05_todo_writer/) → [s06](../s06_subagent/) → [s07](../s07_skill_loader/) → s08 → s09 → s10 → s11 → s12 → s13 → s14 → s15 → s16 → s17

> *「不是所有命令都值得问一遍，但有些命令问都不该问」*

---

## 问题

s02 留下的 `pwsh` 是一扇不上锁的门。模型一条 `Remove-Item -Recurse -Force C:\Users`，
你的用户目录就没了，而你在看到结果之前没有任何机会说「不」。

但反过来，如果每条命令都弹窗问你，这个 agent 就没法用了 —— 它跑十步你按十次 y，
用两天你就会开始无脑按 y，那等于没有门控。

所以问题不是「要不要拦」，而是**怎么分级**。

## 方案

三层，从硬到软：

| 层 | 位置 | 行为 | 典型命令 |
| --- | --- | --- | --- |
| 闸门 1 · 硬拒绝 | `check_deny_list()` | 直接拦下，**不给你审批的机会** | `Clear-Disk`、`bcdedit`、`Stop-Computer`、`-Verb RunAs` |
| 闸门 2 · 规则匹配 | `check_rules()` | 命中则进入闸门 3 | `Remove-Item`、`Set-ItemProperty`、`Invoke-WebRequest` |
| 闸门 3 · 用户审批 | `ask_user()` | 打印工具名和参数，等你按 y | 上面那些，逐条确认 |

为什么闸门 1 不给审批机会？因为有些操作**没有「这次算例外」的说法**。
格盘、删引导配置、关掉 Defender —— 在一个写代码的 agent 场景里，这些永远不会是正当需求，
那么把它交给一个已经被弹窗训练到无脑按 y 的用户去判断，反而是风险。

## 代码怎么走

**第 1 步：硬拒绝名单 —— 字面量部分。**

```python
DENY_LIST = [
    "format-volume", "format.com", "format c:", "clear-disk",
    "initialize-disk", "remove-partition", "diskpart",
    "bcdedit", "wbadmin delete", "vssadmin delete", "delete shadows",
    "remove-item hklm:", "remove-item hkey_local_machine",
    "set-mppreference -disablerealtimemonitoring",
    "set-netfirewallprofile -enabled false",
    "stop-computer", "restart-computer", "logoff",
    "shutdown /s", "shutdown /r", "shutdown /p", "shutdown -s",
]

def check_deny_list(command: str) -> str | None:
    low = command.lower()          # PowerShell 不区分大小写
    for keyword in DENY_LIST:
        if keyword in low:
            return f"'{keyword}' is on the deny list"
    ...
```

两个 PowerShell 特有的坑，都在注释里写着：

1. **命令名不区分大小写** —— `CLear-DISK` 和 `clear-disk` 是同一条命令，
   所以比较前先 `.lower()`。
2. **别名会绕过检查** —— `rm` / `del` / `erase` / `rd` / `ri` / `rmdir` 全都是 `Remove-Item` 的别名。
   只写 `remove-item` 的话，一句 `ri -Recurse -Force C:\` 就能穿过去。

**第 2 步：硬拒绝名单 —— 正则部分。**

有些危险命令要几个词凑齐才成立，或者参数顺序可以随便换，字面量匹配做不到：

```python
DENY_PATTERNS = [
    ("recursive delete of a system location", re.compile(
        r"(?i)\b(?:remove-item|rm|del|erase|rd|ri|rmdir)\b"
        r"(?=[^\n|;]*\s-(?:recurse|r|fo|force)\b)"     # 必须带递归/强制参数
        r"[^\n|;]*?(?:[a-z]:[\\*]{0,2}(?=\s|$|[;|&\"'])"
        r"|c:\\windows\\(?:system32|syswow64|fonts)\b"
        r"|\$env:(?:systemdrive|systemroot|windir)(?=[\\\s*]|$)"
        r"|hk(?:lm|ey_local_machine):)"
    )),
    ("UAC elevation via -Verb RunAs",
     re.compile(r"(?i)\b(?:start-process|saps|start)\b[^\n|;]*\s-verb\s+runas\b")),
    ("privilege escalation via sudo",
     re.compile(r"(?i)(?:^|[;&|]\s*)sudo(?:\.exe)?\s")),
]
```

三条分别对着：**递归删系统位置**、**UAC 提权**、**Windows 11 自带的 sudo.exe**。

`-Verb RunAs` 那条是正则而不是字面量的原因很实际：`-Verb RunAs` 可以放在参数任意位置
（`Start-Process pwsh -Verb RunAs` 和 `Start-Process -Verb RunAs pwsh` 都合法），
固定子串匹配会漏。这个坑在写这章的时候真实踩过一次 —— 先写了字面量版本，被 `Start-Process pwsh -Verb RunAs` 绕过，才补的正则。

**第 3 步：闸门 2 —— 可疑但不拒绝。**

```python
DANGEROUS_PATTERNS = [
    re.compile(r"(?i)\b(?:remove-item|rm|del|erase|rd|ri|rmdir|clear-content|clc)\b"),
    re.compile(r"(?i)\b(?:new-localuser|set-localuser|remove-localuser|add-localgroupmember"
               r"|net\s+(?:user|localgroup)|takeown|icacls|set-acl)\b"),
    re.compile(r"(?i)\b(?:stop-service|set-service|new-service|sc\.exe|schtasks"
               r"|register-scheduledtask|new-scheduledtask)\b"),
    re.compile(r"(?i)\b(?:set-itemproperty|new-itemproperty|remove-itemproperty"
               r"|reg\s+(?:add|delete|import))\b"),
    re.compile(r"(?i)\b(?:invoke-expression|iex|invoke-webrequest|iwr"
               r"|invoke-restmethod|irm|curl|wget)\b"),
    re.compile(r"(?i)\b(?:set-executionpolicy|new-netfirewallrule"
               r"|remove-netfirewallrule|set-netfirewallprofile)\b"),
    re.compile(r"(?i)\b(?:stop-process|spps|taskkill)\b"),
]
```

七个类别：**删文件 / 账号与 ACL / 服务与计划任务 / 注册表写入 / 下载即执行 / 执行策略与防火墙 / 强杀进程**。

这些和闸门 1 的区别是：它们**都有正当用途**。`Remove-Item` 用来清理临时文件很正常，
`Invoke-WebRequest` 用来下载文档很正常。所以它们不该被硬拒，只该被问一句。

注意最后一类里的 `iwr` / `irm` / `curl` / `wget` —— 又是别名问题，和闸门 1 同源。

**第 4 步：把规则和数据分开。**

```python
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
```

这里同时管住了**文件工具的越界**和**命令的危险模式** —— s02 的 `safe_path` 是工具内部的自我防护，
这一层是外部的准入口，两者是独立的。规则表用数据描述，加一条规则不用动 `check_permission` 的逻辑。

**第 5 步：闸门 3 —— 问人。**

```python
def ask_user(tool_name: str, args: dict, reason: str) -> str:
    print(f"\n⚠  {reason}")
    print(f"   Tool: {tool_name}({args})")
    choice = input("   Allow? [y/N] ").strip().lower()
    return "allow" if choice in ("y", "yes") else "deny"
```

默认是 **N**（直接回车就是拒绝）。打印的内容里，「为什么拦」和「拦的是什么」都要有 ——
只问「允许吗」而不说允许什么，用户没法判断。

**第 6 步：接进主循环。**

```python
if not check_permission(tool_call):
    messages.append(ToolMessage(
        content="Permission denied",
        tool_call_id=tool_call["id"],
        name=name,
    ))
    continue
```

被拒绝的调用**也要回一条 `ToolMessage`**，这是容易漏的一步。如果不回，消息列表里就出现了一个
「有 `tool_call` 但没有对应结果」的悬空请求，下一次调 API 会直接报错。回一条 `Permission denied` 进去，
模型还能知道「这一步被挡了」，换个做法继续。

**第 7 步：工具内部再挡一道。**

```python
@tool
def pwsh(command: str) -> str:
    """Run a shell command. On Windows this runs in PowerShell 7."""
    # 工具内部再挡一道：即使调用方绕过了权限门控，硬拒绝名单依然生效
    blocked = check_deny_list(command)
    if blocked:
        return f"Error: {blocked}"
    ...
```

闸门 1 被查了两遍：一次在 `check_permission` 里（为了在你看见之前就拦掉），
一次在工具自己内部。冗余吗？不算 —— s04 之后工具是通过 `TOOLS_HANDERS` 统一调用的，
将来多一个调用入口，工具自带的那道防线仍然成立。

## 跑起来

```bash
uv run python s03_permission/agent_permission.py
```

试试这些：

| 输入 | 预期 |
| --- | --- |
| `列出当前目录的文件` | 直接放行，没有弹窗（`Get-ChildItem` 不在任何名单上） |
| `删掉 tmp 目录` | 弹窗，reason 是 `Potentially destructive command` |
| `清空 C 盘` | **没有弹窗**，直接打 `⛔ hard-denied: recursive delete of a system location` |
| `用管理员权限重启一下 pwsh` | 硬拒绝，命中 `UAC elevation via -Verb RunAs` |
| `读一下 C:\Windows\win.ini` | 弹窗，reason 是 `Access outside workspace` |
| `跑一下 sudo whoami` | 硬拒绝，命中 sudo 提权 |

重点对比前两条：**一个弹窗，一个连弹窗都没有**。这就是「分级」和「一刀切」的区别。

要验证别名有没有被堵死，可以直接测函数：

```bash
uv run python -c "
import sys; sys.path.insert(0, 's03_permission')
from agent_permission import check_deny_list
for c in ['ri -Recurse -Force C:\\\\',
          'rm -r -fo C:/Windows/System32',
          'Remove-Item -Recurse \$env:SystemRoot',
          'Start-Process pwsh -Verb RunAs',
          'sudo rm -rf /']:
    print(repr(c), '->', check_deny_list(c))
"
```

五条应该全部命中。

## 下一步

`check_permission` 现在是**硬编码在主循环里**的。下一章要加「记录每次工具调用」「输出太长时告警」
「退出前打一句总结」这些功能，如果每个都往循环里塞一段 `if`，主循环很快就会变成一团。

→ [s04](../s04_hooks/)：怎么让扩展逻辑挂在循环外面，而循环本身保持干净？
