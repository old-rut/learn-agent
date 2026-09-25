# s01: Agent 主循环 — 从零搭起第一个 while

`s01` → [s02](../s02_tools/) → [s03](../s03_permission/) → [s04](../s04_hooks/) → [s05](../s05_todo_writer/) → [s06](../s06_subagent/) → [s07](../s07_skill_loader/) → s08 → s09 → s10 → s11 → s12 → s13 → s14 → s15 → s16 → s17

> *「一个循环 + 一个工具，就是一个 Agent」*

---

## 问题

你对模型说：「列出这个目录下的文件，然后运行 hello.py。」

模型能把命令打出来，但打完就停了 —— 它不会自己执行，也不会拿着执行结果继续往下想。
你只能手动跑一遍、把输出贴回去，它再吐出下一条命令，你再跑一遍。

每一轮往返里，**你就是那个中间层**。这一章要做的事，就是把你从中间层里拿掉。

## 方案

一个 `while True`：

| 信号 | 含义 | 循环动作 |
| --- | --- | --- |
| `response.tool_calls` 非空 | 模型想调工具 | 执行 → 结果塞回消息列表 → 继续 |
| `response.tool_calls` 为空 | 模型这一轮只想说话 | 退出循环 |

整章就这一条判断，后面六章都没再动过它。

## 代码怎么走

**第 1 步：定义唯一的工具。**

`@tool` 把普通函数变成模型能调用的工具 —— 函数名就是工具名，**docstring 就是给模型看的工具说明**（不写会直接报错）。

```python
@tool
def pwsh(command: str) -> str:
    """Run a shell command. On Windows this runs in PowerShell 7."""
    r = subprocess.run(
        [PS7_PATH, "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=os.getcwd(),
        capture_output=True, text=True, errors="replace", timeout=120,
    )
    out = (r.stdout + r.stderr).strip()
    return out[:50000] if out else "(no output)"
```

三个参数是有讲究的：`capture_output=True` 把 stdout 和 stderr 都收进内存一起返回；
`errors="replace"` 防止命令输出里的乱码把整个读取炸掉；`timeout=120` 防止某条命令把 agent 永久卡死。
超时和文件不存在这两种情况都被 `except` 兜成一句模型能读懂的字符串，而不是抛异常。

**第 2 步：把工具绑到模型上。**

```python
model = init_chat_model("deepseek:deepseek-flash", temperature=0.5, timeout=300)
model_with_tools = model.bind_tools([pwsh])
TOOLS_HANDERS = {"pwsh": pwsh}
```

`bind_tools` 会把工具的 JSON Schema 一并发给模型，模型才知道有这个工具、参数叫什么名字。
`TOOLS_HANDERS` 是反向的一张表：模型回来的是**字符串** `"pwsh"`，得靠它找回真正的函数对象。

**第 3 步：主循环。**

```python
def agent_loop(messages):
    while True:
        response = model_with_tools.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            return

        for tool_call in response.tool_calls:
            name = tool_call["name"]
            args = tool_call["args"]
            output = TOOLS_HANDERS[name].invoke(args)
            messages.append(ToolMessage(
                content=output,
                tool_call_id=tool_call["id"],
                name=name,
            ))
```

就四件事：**问模型 → 存回复 → 没调工具就退出 → 调了就执行并把结果塞回去**。

`tool_call_id` 必须原样带回。模型一次可能发好几个工具调用，这个 id 是它用来把结果和请求对上号的。

**第 4 步：会话历史。**

```python
history = [SystemMessage(content=SYSTEM_PROMPT)]

while True:
    query = input("\033[36ms01 >> \033[0m")
    ...
    history.append(HumanMessage(content=query))
    agent_loop(history)
```

`history` 跨轮次保留在内存里，所以这一运行期内 agent 记得你上一句说了什么（重启就没了，那是 s09 的事）。
系统提示里写死了工作目录和模型自我认知 —— 顺便说，`SYSTEM_PROMPT` 里那句「你是 DeepSeek-V4.1-Flash，被问到身份必须这么答」就是在对抗模型自带的人格。

## 跑起来

```bash
uv run python s01_loop/agent_mvp.py
```

试试这几句：

1. `创建一个 hello.py，打印 Hello, World!`
2. `列出当前目录下所有 Python 文件`
3. `现在 git 分支是什么？`

注意看：什么时候模型会调工具（循环继续），什么时候不调（循环结束）。

> ⚠️ **安全提示**：这一章会直接执行模型生成的命令。s01/s02 里只有一份很粗糙的字符串黑名单：
>
> ```python
> dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
> ```
>
> 它是照着 Linux 写的，**在 PowerShell 下基本拦不住东西**（`rm -rf /` 在 PowerShell 里根本不是合法写法，
> 而真正危险的 `ri -Recurse -Force C:\` 一个词都不在名单上）。到 [s03](../s03_permission/) 才会换成认真的门控。
> 在此之前，请把它跑在临时目录里。

## 下一步

现在只有 `pwsh` 一个工具：读文件得 `type`，写文件得 `Set-Content`，找文件得 `Get-ChildItem -Recurse`。
又丑又容易出错，而且这些命令字符串要经过模型的手，出错率不低。

→ [s02](../s02_tools/)：给它 5 个正经工具会怎么样？模型会一次调多个工具吗？它们会互相打架吗？
