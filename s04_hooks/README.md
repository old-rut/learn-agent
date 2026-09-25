# s04: Hooks — 把扩展逻辑挂在循环外面

[s01](../s01_loop/) → [s02](../s02_tools/) → [s03](../s03_permission/) → `s04` → [s05](../s05_todo_writer/) → [s06](../s06_subagent/) → [s07](../s07_skill_loader/) → s08 → s09 → s10 → s11 → s12 → s13 → s14 → s15 → s16 → s17

> *「主循环只负责流转，不负责策略」*

---

## 问题

s03 的 `check_permission(tool_call)` 是**硬编码在主循环里**的：

```python
if not check_permission(tool_call):     # ← 直接写在循环中间
    messages.append(ToolMessage(content="Permission denied", ...))
    continue
```

现在你还想加几件事：每次工具调用打一行日志、工具输出太长时告警、退出前统计用了多少次工具、
用户提交问题时注入一点上下文。四件事，四段 `if`，全塞进同一个 `while` 里。

问题不在于「循环变长了」，而在于**每次调整策略都要动循环**。日志格式想改，得开循环；
权限判断想换个顺序，得开循环。循环本来是整章唯一不该动的东西，结果成了改动最频繁的地方。

## 方案

给循环定义几个**事件**，让扩展逻辑注册到事件上。循环只负责在正确的时刻喊一声，
至于有谁在听、听了做什么，它一概不管。

| 事件 | 触发时机 | 传进去的参数 |
| --- | --- | --- |
| `UserPromptSubmit` | 用户敲完回车、消息还没进历史 | `query` |
| `PreToolUse` | 工具执行**之前** | `tool_call` |
| `PostToolUse` | 工具执行**之后** | `tool_call`, `output` |
| `Stop` | 模型不再调工具、循环即将退出 | `messages` |

约定很简单：**钩子返回 `None` = 我不管，继续；返回非 `None` = 我拦下了，这就是理由。**

## 代码怎么走

**第 1 步：注册表 + 触发器，一共八行。**

```python
HOOKS = {"UserPromptSubmit": [], "PreToolUse": [], "PostToolUse": [], "Stop": []}

def register_hook(event: str, callback):
    HOOKS[event].append(callback)

def trigger_hooks(event: str, *args):
    for callback in HOOKS[event]:
        result = callback(*args)
        if result is not None:  # A hook result blocks this tool call.
            return result
    return None
```

「第一个返回非 `None` 的钩子获胜，后面的不再执行」—— 这一条约定同时管住了三件事：
**拦截**（PreToolUse 返回拒绝理由）、**早退**（不用跑无关的钩子）、**短路顺序**（注册顺序 = 优先级）。

**第 2 步：五个钩子，各管一件事。**

```python
# UserPromptSubmit：注入上下文
def context_inject_hook(query: str):
    print(f"\033[90m[HOOK] UserPromptSubmit: working in {WORKDIR}\033[0m")
    return None

# PreToolUse：权限门控（从 s03 搬过来的，逻辑一行没改）
def permission_hook(tool_call) -> str | None:
    ...
    return None

# PreToolUse：日志
def log_hook(tool_call: dict):
    name = tool_call.get("name", "unknown")
    args = tool_call.get("args") or {}
    args_preview = str(list(args.values())[:2])[:60]
    print(f"\033[90m[HOOK] {name}({args_preview})\033[0m")
    return None

# PostToolUse：大输出告警
def large_output_hook(tool_call, output):
    if len(str(output)) > 10000:
        print(f"\033[33m[HOOK] Large output from {tool_call['name']}: {len(str(output))} chars\033[0m")
    return None

# Stop：退出前统计
def summary_hook(messages: list):
    tool_count = sum(
        len(m.tool_calls)
        for m in messages
        if isinstance(m, AIMessage) and m.tool_calls
    )
    print(f"\033[90m[HOOK] Stop: session used {tool_count} tool calls\033[0m")
    return None
```

`permission_hook` 是从 s03 的 `check_permission` 直接搬过来的 —— **`check_deny_list` / `PERMISSION_RULES` / `ask_user` 一个字没改**。

这点值得留意：s03 里那三百行权限代码，在这一章里变成了「注册表里的一行」。

**第 3 步：把它们挂上去。**

```python
register_hook("UserPromptSubmit", context_inject_hook)
register_hook("PreToolUse", permission_hook)
register_hook("PreToolUse", log_hook)
register_hook("PostToolUse", large_output_hook)
register_hook("Stop", summary_hook)
```

同一个事件可以挂多个钩子，按注册顺序执行。

注意 `permission_hook` 排在 `log_hook` **前面** —— 这是有意的：权限拒绝时返回非 `None`，`log_hook` 根本不会执行。
也就是说**被拦下的调用不会留下日志**。想改的话把两行顺序对调即可，这正是「注册顺序 = 优先级」的用处。

**第 4 步：循环里只留下三个提问点。**

```python
def agent_loop(messages):
    while True:
        response = model_with_tools.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            force = trigger_hooks("Stop", messages)      # ← 提问点 3
            if force:
                messages.append(HumanMessage(content=force))
                continue
            return

        for tool_call in response.tool_calls:
            name = tool_call["name"]
            args = tool_call["args"]
            tool_called = trigger_hooks("PreToolUse", tool_call)   # ← 提问点 2
            if tool_called:
                messages.append(ToolMessage(content=str(tool_called),
                                            name=name,
                                            tool_call_id=tool_call["id"]))
                continue
            output = TOOLS_HANDERS[name].invoke(args)
            trigger_hooks("PostToolUse", tool_call, output)        # ← 提问点 2
            messages.append(ToolMessage(...))
```

对比 s03：循环里不再有 `check_permission`、不再有提示音、不再有统计。
整个循环的**控制流**只剩下四处：调模型、判断有没有工具调用、执行工具、把结果塞回去。

`trigger_hooks` 的返回值在哪里用、怎么用，是循环决定的；至于会返回什么，循环不知道也不关心。

**第 5 步：`Stop` 事件的特殊之处。**

```python
force = trigger_hooks("Stop", messages)
if force:
    messages.append(HumanMessage(content=force))
    continue        # 不 return，继续循环
```

前三个事件的返回值都是「拦截」，唯独 `Stop` 的返回值是「**再跑一轮**」。
这是个刻意留下的口子：钩子可以在模型想收工时塞一句话进去把它拽回来。
s05 的 todo 催促提醒走的就是这条路，只是那一次直接写在循环里没走钩子。

## 跑起来

```bash
uv run python s04_hooks/main_loop.py
```

随便问点什么，比如 `列出当前目录的文件`，观察钩子按什么顺序打印：

```
[HOOK] UserPromptSubmit: working in D:\PyCharmProjects\agent-learn\s04_hooks
$ 
[HOOK] pwsh(['Get-ChildItem'])
...命令输出...
[HOOK] Stop: session used 1 tool calls
```

顺着看一遍：**提交 → 拦截？→ 执行 → 事后 → 退出前**。五个钩子在这条链上的位置一目了然。

再试 `删掉 tmp 目录`，会看到 `permission_hook` 弹出确认框 —— 而且注意，
**这次没有 `[HOOK] pwsh([...])` 那行日志**，因为 `permission_hook` 抢先返回了。

## 目录结构的变化

这一章第一次把单文件拆成了包：

```
s04_hooks/
├── config.py                   WORKDIR / PS7_PATH
├── tools.py                    5 个工具 + model + TOOLS_HANDERS
├── hooks/
│   ├── trigger_hooks.py        注册表 + 4 个事件的接线
│   ├── permission_hook.py      s03 的门控逻辑，原样搬过来
│   └── other_hooks.py          日志 / 大输出 / 上下文 / 统计
└── main_loop.py                主循环 + 交互
```

导入写的是 `from hooks.trigger_hooks import trigger_hooks` 这种**绝对导入**。
原因见[根 README](../README.md#两个容易踩的点)：这样 PyCharm 能直接运行入口文件，
代价是 `uv run python -m s04_hooks.main_loop` 会失败。

## 下一步

钩子解决了「扩展逻辑往哪放」，但模型自己还是**没有计划能力** —— 你让它做一件三步的事，
它可能做完第一步就开始总结，或者来回绕圈。而且它从不告诉你「我打算做什么」。

→ [s05](../s05_todo_writer/)：怎么让模型把计划**写成一个结构化对象**，而不是散落在自然语言里？
