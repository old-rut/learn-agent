# s06: Subagent — 把探索过程留在另一个上下文里

[s01](../s01_loop/) → [s02](../s02_tools/) → [s03](../s03_permission/) → [s04](../s04_hooks/) → [s05](../s05_todo_writer/) → `s06` → [s07](../s07_skill_loader/) → s08 → s09 → s10 → s11 → s12 → s13 → s14 → s15 → s16 → s17

> *「子代理不是更聪明的 agent，是一个更干净的上下文」*

---

## 问题

s05 让模型学会写计划了。但计划里的每一步都**在当前对话的上下文里执行**。

假设某一步是「搞清楚这个项目的错误处理约定」——子代理可能要读 20 个文件才发现
「全都是返回 `f"Error: {e}"` 字符串，不抛异常」。这 20 份文件内容**全部**留在了主对话历史里：

- 这一轮之后，每一轮 API 调用都要重新发送这 20 份文件
- 上下文窗口被塞满，后面真正重要的信息被挤出去
- 而整趟探索真正有用的产出，只有一句话

问题不是「模型不够聪明」，是**探索过程本身污染了主上下文**。

## 方案

把一步子任务交给一个**全新的消息列表**去跑，跑完只把最终结论作为一条字符串返回。

```
主上下文                             子上下文（一次性）
─────────────                       ──────────────────
[System, Human, AI, Tool, AI, ...]  [System, Human]
         │                                   │
         │  subagent("调研错误处理约定")       │
         └──────────────────────────────────►│
                                             │  AI → Tool → AI → Tool → ...  (最多 30 轮)
                                             │
         ◄───────────────────────────────────┘
         │                    "全部工具返回 f'Error: {e}' 字符串，不抛异常"
         ▼
   ToolMessage(那一句话)          ← 20 份文件内容一个都没进来
```

关键在于：子代理和主代理之间**只有一次字符串进出**。它看不到主对话，主对话也看不到它的过程。

## 代码怎么走

**第 1 步：子代理本身就是一个普通的 agent 循环。**

```python
@tool
def subagent(prompt: str) -> str:
    """execute one self-contained subtask in a fresh context and return only its final result"""
    messages = [SystemMessage(content=SUB_SYSTEM)]
    messages.append(HumanMessage(content=prompt))

    for _ in range(30):
        response = model_with_tools.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            ...
            return extract_text(response.content) or "(no summary)"

        for tool_call in response.tool_calls:
            output = execute_tool(tool_call, SUB_HANDERS)
            messages.append(ToolMessage(...))

    return "Subagent stopped after 30 turns without a final answer."
```

和 s01 的主循环结构完全一样，只有三处不同：

1. **`messages` 是新建的**，不是外面传进来的 —— 这一行就是「上下文隔离」的全部实现。
2. **有 30 轮上限** —— 主循环是 `while True`，靠模型自己决定什么时候停；子代理必须有个硬上限，
   否则一个跑偏的子任务会无限烧下去，而且你在主界面上看不到它在干什么。
3. **返回值是一句话**，不是完整的 `messages`。

**第 2 步：`extract_text` —— 内容不一定是字符串。**

```python
def extract_text(content) -> str:
    if not isinstance(content, list):
        return str(content)
    parts = []
    for block in content:
        # LangChain 的块可能是 dict，也可能是带属性的对象
        btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if btype == "text":
            text = block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
            parts.append(text)
    return "\n".join(parts)
```

`response.content` 有两种形态：纯文本模型返回 `str`，带多模态/工具块的模型返回 `list[block]`。
这里两种都接住，而且每个 block 又可能是 **dict** 或**带属性的对象**（取决于 LangChain 版本和 provider），
所以每个取值都写了两条路。

> 写这章的时候踩过一个坑：这里最早写成 `extract_text(response)` —— 把整个 `AIMessage` 传进去了，
> 而不是 `response.content`。`isinstance(AIMessage, list)` 是 `False`，于是走了第一行 `str(content)`，
> 子代理最后返回的是对象的一长串 repr（含 `response_metadata`、`usage_metadata` 等）。
> 子代理确实「返回了东西」，所以不会报错，只是回来的内容全是噪音。**这类 bug 不会崩，只会静默地变糟**，尤其难发现。

**第 3 步：主代理怎么知道该不该派子代理？**

子代理是强大的，但不是免费的 —— 它多一次完整对话的开销，而且**看不到你和用户聊了什么**。
所以系统提示里把「什么时候用」「什么时候不用」都写清楚了：

```python
When to use `subagent`:
- A todo item can be fully described in one self-contained prompt.
- Executing it would flood this conversation with tool output.
- It does not depend on user feedback or on the current conversation's context.

When NOT to use `subagent`:
- The overall user request. You keep ownership; `subagent` only executes a piece.
- Steps that need clarification, user confirmation, or multiple rounds with the user.
- Simple single-step operations (one read, one command, one edit).
- Steps that depend on the current conversation or on user feedback.
```

最后那段话是重点：

```
The subagent cannot see our conversation and cannot ask the user questions,
so each `subagent` prompt must be fully self-contained and must specify exactly
what result to return.
```

**「self-contained」是使用子代理的唯一硬性要求。** 派发的时候不能写「按刚才说的改一下」，
必须把前因后果、文件路径、期望输出全部重新说一遍 —— 因为对子代理来说，「刚才」不存在。

**第 4 步：循环导入 —— 这一章真正花时间的地方。**

加完 `subagent` 之后，导入关系成了这样：

```
tools_register.py  ──►  tools/subagent.py
       ▲                        │
       │                        ▼
       └──────────────── utils/execute_tool.py
```

`tools_register` 要 `subagent` 才能注册工具；`subagent` 要 `execute_tool` 才能执行子任务的工具；
而 `execute_tool` 又要 `tools_register` 才能拿到 `TOOLS_HANDERS`。**转了一圈。**

Python 遇到这种环不会报错，而是给你一个**初始化到一半的模块** —— 于是报错变成
`ImportError: cannot import name 'X' from partially initialized module`，或者某个名字是 `None`。
更麻烦的是，这类报错**和导入顺序有关**：换个入口文件可能就好了，而你没改任何逻辑。

两处改动拆掉了这个环：

**（a）`execute_tool` 改成依赖注入，不再自己去 import 注册表。**

```python
# utils/execute_tool.py
def execute_tool(tool_call, handers) -> str:
    ...
    output = handers[name].invoke(args) if name in handers else f"Unknown tool: {name}"
```

谁调用谁负责把 `handers` 传进来。`execute_tool` 从此不知道自己被谁用、也不知道有哪些工具 ——
**它从环上的一个节点，变成了一个可以被任何人调用的工具函数。**

**（b）抽出叶子模块 `tools/base_tools.py`。**

```python
# tools/base_tools.py —— 不 import 任何同项目模块（除了 tools.* 里的叶子）
from tools.file_tools import read_file, write_file, edit_file, glob_files
from tools.pwsh import pwsh

BASE_TOOLS = [pwsh, read_file, write_file, edit_file, glob_files]
BASE_HANDERS = {t.name: t for t in BASE_TOOLS}
```

主代理和子代理需要的工具集**是同一批**，但 `subagent.py` 不能去 import `tools_register`。
把这份清单下沉到一个谁都不依赖的叶子模块里，两边就都能安全地 import 它：

```python
# tools/tools_register.py
TOOLS = BASE_TOOLS + [subagent] + [todo_writer]
```

> 为什么不用「调整 import 顺序」这个更省事的办法？
> 因为 ruff / isort 这类格式化工具会**按字母序重排 import**，然后这个环就被重新接上了 ——
> 你的代码在本地跑得好好的，同事的编辑器一保存就炸。**依赖结构的问题要用结构解决，不能用书写顺序解决。**

**第 5 步：还有两个导入顺序的坑。**

```python
from dotenv import load_dotenv
...
load_dotenv()                       # ← 必须在下面那个 init_chat_model 之前

model = init_chat_model("deepseek:deepseek-flash", ...)
```

`subagent.py` 在**模块顶层**创建了模型。这意味着：**任何 import 了 `subagent` 的地方，
都会顺带触发一次模型创建**，而 `init_chat_model` 需要环境里已经有 `DEEPSEEK_API_KEY`。

所以 `load_dotenv()` 必须排在模型创建之前。放在文件末尾会得到：

```
ValidationError: DEEPSEEK_API_KEY must be set
```

而且这个错**看起来跟 import 一点关系都没有**。最后在 `main_loop.py` 和 `subagent.py`
这两个创建模型的文件里各调一次 `load_dotenv()`（重复调用无害），才算各种入口顺序都稳。

## 跑起来

```bash
uv run python s06_subagent/main_loop.py
```

找一个**确实需要读很多文件才能回答**的问题，才能看出子代理的价值：

```
统计一下这个项目里所有 @tool 装饰的函数分别定义在哪个文件，各自有几个
```

预期看到：

```
[HOOK] todo_writer([...])
## Current Tasks
[>] 定位所有 @tool 定义
[ ] 汇总每个文件的工具数量

[Subagent started]
$ 
  [sub] glob_files: s01_loop/agent_mvp.py
  [sub] read_file: ...
  [Subagent done]
[Subagent stopped]

[HOOK] Stop: session used N tool calls
```

重点观察**两件事**：

1. `[sub]` 打头的那些行 —— 那是子代理**内部**的工具调用。它们全部发生在子上下文里，
   主对话历史里**一条都没有**。
2. 子代理返回后，主上下文里只有一条 `ToolMessage`，内容是那句结论。

反过来再试一个**不该派子代理**的任务，比如 `读一下 config.py`，
它应该直接自己做 —— 派子代理反而更慢。

## 目录结构的变化

```
s06_subagent/
├── config.py
├── tools/
│   ├── base_tools.py        ★ 叶子模块：BASE_TOOLS + BASE_HANDERS
│   ├── file_tools.py
│   ├── pwsh.py
│   ├── todo_writer.py
│   ├── subagent.py          ★ 独立的模型实例 + SUB_HANDERS
│   └── tools_register.py    TOOLS = BASE_TOOLS + [subagent] + [todo_writer]
├── utils/
│   └── execute_tool.py      ★ 改成依赖注入
├── hooks/
└── main_loop.py
```

## 下一步

到这一章，agent 的能力是够了，但**系统提示里能塞的东西是有上限的**。
每加一项技能就往 `SYSTEM_PROMPT` 里写一大段说明，会挤占上下文；不写，模型又不知道有这项技能。

→ [s07](../s07_skill_loader/)：怎么让技能说明**平时只占一行**，需要的时候才展开？
