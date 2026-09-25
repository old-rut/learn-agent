# s05: TodoWriter — 让模型把计划写成一个对象

[s01](../s01_loop/) → [s02](../s02_tools/) → [s03](../s03_permission/) → [s04](../s04_hooks/) → `s05` → [s06](../s06_subagent/) → [s07](../s07_skill_loader/) → s08 → s09 → s10 → s11 → s12 → s13 → s14 → s15 → s16 → s17

> *「计划如果只存在于自然语言里，它就不可追踪」*

---

## 问题

你让模型做一件三步的事，它可能：

- 做完第一步就写一段总结收工了，剩下两步忘了
- 在第二步和第三步之间来回绕，第六轮才发现第一步其实没做完
- 全程不告诉你它打算做几步，你只能看着它一条条命令打出来，心里没底

根源是：**计划只存在于模型的自然语言输出里**。「我打算先看看目录，然后读一下配置，最后改掉那一行」
—— 这句话读完就从上下文里滑走了，下一轮它不会回去对照，你也没法追踪。

## 方案

把计划变成一个**有 schema 的对象**，让模型显式地写下来，并且每轮都能看到当前状态：

```json
[
  {"content": "读取 config.py 找到 PS7_PATH", "status": "completed"},
  {"content": "把它改成从环境变量读取",       "status": "in_progress"},
  {"content": "跑一遍冒烟测试",               "status": "pending"}
]
```

状态只有三个值：`pending` / `in_progress` / `completed`。工具每被调用一次，就把整份列表**整体覆盖**一次，
然后回显渲染好的清单。

这样做的收益是双份的：**模型**在每一轮的上下文里都能看到自己走到哪了；
**你**也能在终端上看到同一份进度。

## 代码怎么走

**第 1 步：用 Pydantic 定义一条待办。**

```python
class Todo(BaseModel):
    """一条待办。字段约束会自动进入工具的 JSON Schema，模型可以直接看到。"""
    content: str = Field(min_length=1, description="what needs to be done")
    status: Literal["pending", "in_progress", "completed"] = Field(
        default="pending", description="current state"
    )
```

**这是整章最核心的一步。** 用 Pydantic 模型而不是 `TypedDict` 或裸 `dict`，是因为类型标注会**自动生成 JSON Schema 发给模型**：

```json
"todos": {
  "anyOf": [{
    "items": {
      "description": "一条待办。字段约束会自动进入工具的 JSON Schema，模型可以直接看到。",
      "properties": {
        "content": {"description": "what needs to be done", "minLength": 1, "type": "string"},
        "status": {
          "default": "pending",
          "description": "current state",
          "enum": ["pending", "in_progress", "completed"],
          "type": "string"
        }
      },
      "required": ["content"],
      "type": "object"
    },
    "maxItems": 20,
    "type": "array"
  }, {"type": "string"}]
}
```

看 `enum` —— 那三个合法状态是**模型在调用前就看得见**的。它不需要靠猜、也不需要靠试错被拒一次才知道该填什么。
这就是为什么约束要写在类型里，而不是写在函数的 `if` 里：写在类型里模型能看见，写在 `if` 里模型只能撞上去。

**第 2 步：为什么参数是 `list[Todo] | str`。**

```python
@tool
def todo_writer(todos: Annotated[list[Todo], Field(max_length=20)] | str) -> str:
    """Create and manage a task list for your current coding session."""
```

`max_length=20` 是 `Annotated` 附加到 `list` 上的整体约束，生成出来就是上面那个 `"maxItems": 20`。

那个 `| str` 是妥协的产物：模型有一定概率**不按结构走，而是把整份 JSON 当成一个字符串塞进来**。
与其让它撞一次校验错误再重试，不如在 schema 层就允许两种形式，把归一化放到函数里做：

```python
if isinstance(todos, list):
    # LangChain 已按 Todo 模型校验并实例化，转回 dict 交给 update() 做业务规则校验
    todos = [t.model_dump() if isinstance(t, Todo) else t for t in todos]
```

注意 `model_dump()`：LangChain 传进来的是**已经实例化的 `Todo` 对象**，不是 dict。
`update()` 期望 dict，所以这里转一道。这个类型转换不写的话，后面 `todo.get("content")` 会直接 `AttributeError`。

**第 3 步：schema 管不了的规则，写在代码里。**

```python
class TodoManager:
    def update(self, todos: list | str) -> str:
        if isinstance(todos, str):
            try:
                todos = json.loads(todos)
            except json.JSONDecodeError:
                try:
                    todos = ast.literal_eval(todos)
                except (SyntaxError, ValueError) as e:
                    raise ValueError("todos must be a list or JSON array string") from e
        ...
        if in_progress_count > 1:
            raise ValueError("Only one todo can be in_progress at a time")
```

关键的分界线在这里：**能表达成 JSON Schema 的（长度、枚举、必填）交给 Pydantic，
表达不了的（「同时只能有一条 in_progress」）只能手写。**

`maxItems: 20` 是 schema 说的，模型看得见；而「只能有一条 in_progress」是一条**跨元素的约束**，
JSON Schema 描述不了单个元素之外的关系，模型看不见，只能等它违反的时候被拒一次。

字符串分支里的 `json.loads` → `ast.literal_eval` 兜底也值得看：模型偶尔会吐 Python 字面量风格的
`[{'content': 'a', 'status': 'pending'}]`（单引号），`json.loads` 吃不下去，`literal_eval` 可以。
但 `literal_eval` **只解析字面量，不执行代码**，所以用它是安全的。

**第 4 步：把状态渲染回模型能读的形式。**

```python
def render(self) -> str:
    lines = []
    for todo in self.items:
        marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}[todo["status"]]
        lines.append(f"{marker} {todo['content']}")
    done = sum(todo["status"] == "completed" for todo in self.items)
    lines.append(f"\n({done}/{len(self.items)} completed)")
    return "\n".join(lines)
```

```
[>] 读取 config.py 找到 PS7_PATH
[ ] 把它改成从环境变量读取
(0/2 completed)
```

渲染成文本，是因为 `ToolMessage` 的 `content` 最终是给模型读的字符串。
末尾那个 `(0/2 completed)` 是给模型的一个廉价进度信号 —— 它比逐行读状态更容易触发「我还有两件事没做完」。

**第 5 步：主循环里的计数器。**

```python
def agent_loop(messages):
    rounds_since_todo = 0
    while True:
        response = model_with_tools.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            ...  # s04 的 Stop 钩子原样保留
            return

        used_todo = False
        for tool_call in response.tool_calls:
            ...
            if tool_call["name"] == "todo_writer":
                used_todo = True
            messages.append(ToolMessage(...))

        rounds_since_todo = 0 if used_todo else rounds_since_todo + 1
        if rounds_since_todo >= 3:
            messages.append(HumanMessage(
                content="<reminder>Update your todos.</reminder>"
            ))
            rounds_since_todo = 0
```

三个细节：

1. **`used_todo` 是整轮统计的**，不是单次调用。一轮里模型调了 5 个工具，只要其中一个是 `todo_writer`，
   这一轮就算「更新过」。
2. **`rounds_since_todo` 在循环外初始化**，所以它跨轮次累加 —— 你说的每一句话都在同一个 `agent_loop` 调用链里，
   计数是连续的。
3. **触发后立刻归零**，否则每一轮都会重新塞提醒，把上下文灌满。

那对 `<reminder>` 标签是给模型的位置提示：它知道这不是用户说的话，而是一句系统级的催促。
用 `HumanMessage` 承载是权宜之计（`ToolMessage` 需要配对的 `tool_call_id`，`SystemMessage` 插在中间又很怪），
Claude Code 自己也用类似的做法。

**第 6 步：错误别再抛出去。**

```python
try:
    output = TOOLS_HANDERS[name].invoke(args) if name in TOOLS_HANDERS else f"Unknown tool: {name}"
except Exception as e:
    output = f"Error: {e}"
```

这一层的意义和 s02 里每个工具内部的 `try` 一样，只是**粒度更粗**：工具内部管不住的异常
（Pydantic 校验失败、`AttributeError` 之类）在这里被统一接住，转成一句模型读得懂的字符串。

实测这几种输入会走到这里：

| 输入 | 模型收到 |
| --- | --- |
| 两条 `in_progress` | `Error: Only one todo can be in_progress at a time` |
| `status: "x"` | `Error: 2 validation errors for todo_writer ...` |
| `content: ""` | `Error: ... String should have at least 1 character` |
| 25 条待办 | `Error: ... List should have at most 20 items` |

每一种都是**模型能自己看懂并改正**的。不接这一层的话，任何一种都会直接把程序打断。

## 跑起来

```bash
uv run python s05_todo_writer/main_loop.py
```

**用一件真的需要多步的事来测**，单步任务它不会去用 todo：

```
把 s05_todo_writer/tools/pwsh.py 里的 PS7_PATH 改成从环境变量读，改完验证一下还能跑
```

预期看到：

```
[HOOK] todo_writer([{'todos': [...]}])
## Current Tasks
[>] 读取 pwsh.py 确认当前 PS7_PATH 用法
[ ] 改成 os.getenv 读取并加上默认值
[ ] 运行一次确认没有语法错误

(0/3 completed)
```

然后观察它做完一步后会不会主动回来更新状态。**如果不更新，三轮之后你会看到 `rounds_since_todo` 把催促塞进去的效果。**

想直接看校验行为，可以绕过模型手动调：

```bash
uv run python -c "
import sys; sys.path.insert(0, 's05_todo_writer')
from tools.todo_writer import todo_writer
print(todo_writer.invoke({'todos': [{'content':'a','status':'in_progress'},
                                    {'content':'b','status':'in_progress'}]}))
"
```

## 目录结构的变化

工具从一个 `tools.py` 拆成了包：

```
s05_todo_writer/
├── config.py
├── tools/
│   ├── file_tools.py        read / write / edit / glob
│   ├── pwsh.py
│   ├── todo_writer.py       Todo 模型 + TodoManager + @tool
│   └── tools_register.py    model + TOOLS + TOOLS_HANDERS
├── hooks/                   与 s04 相同
└── main_loop.py
```

`todo_writer.py` 现在是整个项目最长的单个文件（九十来行），因为它同时装着**数据模型**、
**业务规则**和**工具入口**三样东西。这三样放在一起是有道理的：改 schema 的时候，
校验规则和渲染逻辑通常要跟着改。

## 下一步

todo 让模型有了计划，但计划里的**每一步仍然在当前对话的上下文里执行**。
如果某一步要读 30 个文件才能得出结论，那 30 份文件内容会永久留在对话历史里 ——
哪怕这个结论只有一句话有用。

→ [s06](../s06_subagent/)：怎么让一步子任务跑在**另一个上下文**里，只把结论带回来？
