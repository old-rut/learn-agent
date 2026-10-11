# s09: Memory — 让重要信息跨会话保留下来

[s01](../s01_loop/) → [s02](../s02_tools/) → [s03](../s03_permission/) → [s04](../s04_hooks/) → [s05](../s05_todo_writer/) → [s06](../s06_subagent/) → [s07](../s07_skill_loader/) → [s08](../s08_context_compact/) → `s09` → s10 → s11 → s12 → s13 → s14 → s15 → s16 → s17

> *"把以后还会用到的信息留下来。"* — 文件存储 + 索引 + 相关性选择 + 按需召回。
>
> **Harness 层**: Memory — 在会话之外保存可复用知识，并在相关任务中取回。

---

## 问题

s08 把压掉的东西**原样留在了磁盘上**：`.transcripts/` 里躺着一份完整记录，`.tool-results/` 里躺着工具结果原文。

但模型不知道它们存在，也不会主动去翻。

于是有些东西每次会话都要重新讲一遍：

- 用户上周说过的偏好（"回答别加 emoji"、"用中文回答"）
- 项目里约定俗成的做法（"这个仓库只在 Windows 上验证"）
- 踩过一次的坑（"uv add 会改 pyproject，别用"）

这些都是**跨会话**的。s08 解决的是"这一轮塞不下"，没解决"下一轮想不起来"。

而"记住"不是把对话存档就完事，有三个真问题挡在前面：

1. **什么值得留。** 工具输出、临时状态、本次会话的路径 —— 这些留下去是负债。
2. **怎么不越存越烂。** 重复的、过时的、互相矛盾的，会随时间堆积。
3. **什么时候取回来。** 全量塞回上下文等于 s08 白做。

## 方案

> **两个钩子挂在循环两端：进模型前按当前请求召回几条注入系统提示词，回合结束后从对话里提炼值得长期保留的写进磁盘。**

```
用户提问
   ↓
[PreAgent]  recall_memory  ──→ 选几条 + 注入 SystemMessage
   ↓
   agent_loop（和 s08 一模一样）
   ↓
[PostAgent] extract_memory ──→ 提炼 + 入去重闸门 + 攒够就合并
```

| 事件 | 钩子 | 干什么 | 什么时候 |
| --- | --- | --- | --- |
| `PreAgent` | `load_memories` | 选出相关记录，拼进系统提示词 | 每轮提问、进模型**之前** |
| `PostAgent` | `store_memories` | 从对话提炼新记录，写盘 | 一个回合**结束**、没有工具调用了 |

两个事件都挂在 s04 那套 `register_hook` 上 —— 主循环**只知道有两个钩子事件**，不知道"记忆"是什么。

### 核心原理：读路径和 RAG 同形，写路径才是记忆独有的

"取回相关文本塞进上下文"，记忆和 RAG 是同一个形状。真正不一样的在后半段：

| | 检索（RAG） | 记忆 |
| --- | --- | --- |
| 语料谁写 | 人／系统，离线 | **agent 自己，从自己的对话里** |
| 写入路径 | 没有 | **全部难点在这**：该不该记、去重、合并、纠错 |
| 会不会变 | 基本不变 | **会变**（本科二年级 → 三年级） |
| 检索错了 | 引一段无关文字，看得见 | **错的东西注入每一轮系统提示词**，静默塑造一切 |

所以这一章九成的代码在写路径上 —— `validate_memory_record`（形状）、
`should_store_memory`（去留）、三重去重、`TEMPORARY_MEMORY_MARKERS`、
`consolidate_memories`（合并纠错）。这套东西 RAG 一份都没有。

**换句话说：记忆 ≈ RAG 的读 + 一整套 RAG 没有的写。**

## 代码怎么走

**第 1 步：先把它挂上去 —— 循环只多两行。**

```python
def agent_loop(messages):
    rounds_since_todo = 0
    trigger_hooks("PreAgent", messages)            # ★ 多这一行
    while True:
        response = model_with_tools.invoke(messages)
        ...
        if not response.tool_calls:
            force = trigger_hooks("Stop", messages)
            if force:
                messages.append(HumanMessage(content=force))
                continue
            trigger_hooks("PostAgent", messages)   # ★ 多这一行
            return
```

`agent_loop` 从 s08 的 **33 行变成 35 行**，多出来的就是这两句。循环结构、
工具执行、todo 催促，一行没动。

**这是 s08 那套「循环是骨架，机制是挂件」的又一次验证** —— 而且这次连中间件都没用上，
一个 s04 就有的钩子事件表就够了。

**第 2 步：存什么 —— 形状和去留分成两层。**

两道闸门职责严格分开，不重叠：

| 函数 | 管什么 | 判据 |
| --- | --- | --- |
| `validate_memory_record` | **形状** | 是 dict、四个字段非空、`type` 在词表里 |
| `should_store_memory` | **去留** | `scope == "persistent"`、不含临时字眼、不重复 |

`validate` 顺手做归一化（strip 后返回新 dict），`should_store` 只管放不放行。
中间那个 `scope` 字段由两者交接：**前者原样带过，后者裁决**。

> **踩到的坑：一个不是 bug 的 bug。**
>
> `validate_memory_record` 原来有个 `require_scope` 参数，`extract_memories` 传 `True`。
> 它放行 `{persistent, current_task}` 两个值 —— 而下一行 `should_store_memory`
> 只留 `persistent`。
>
> 也就是第一道闸门刚放行的东西，第二道立刻枪毙。把参数删掉、逐条比对最终去留，
> 54 组边界用例**结果完全一致** —— 它是个装饰品：看起来在管 scope，其实什么都不管。

**第 3 步：什么不该存。**

`TEMPORARY_MEMORY_MARKERS` 有 19 条，中英日三语，因为这一层挡的是**模型的判断**，
不是用户的输入：

```python
"this session", "current session", "for now", "just this time", "today only",
"本次会话", "当前任务", "暂时", ...
```

命中就拒。理由是这些东西**下周就不成立了**，而记忆是给下周用的。

**第 4 步：怎么不越存越烂 —— 三重去重。**

```
同 slug？           memory_slug(name) 归一后一样        → 拒
同 description？    _normalized_memory_text 后全等      → 拒
同 body？           同上                                → 拒
```

`_normalized_memory_text` 做的是 `" ".join(value.lower().split())` —— 大小写和空白差异
不该算两条。

> **顺带堵掉的一个洞：`memory` 这个名字。**
>
> `memory_slug("memory")` 得到 `"memory"`，文件名就是 `memory.md`。而索引是 `MEMORY.md`。
> 守卫写的是 `filename == MEMORY_INDEX.name` —— **字符串比较，大小写敏感**。
>
> Windows/macOS 的文件系统不敏感，所以 `memory.md` 就是 `MEMORY.md`：
> `write_memory_file` 把记录写进去，紧接着 `rebuild_memory_index()` 又把它覆盖回索引。
> 结果**函数返回成功、路径看着正常、记录消失、不报错**。
>
> 现在两处都堵了：`memory_slug` 不再产出索引名，`memory_path` 改成大小写不敏感比较。

**第 5 步：攒够了就合并 —— 但先快照。**

`CONSOLIDATE_THRESHOLD = 10`：库里攒到 10 条以上、**且这一轮确实存进了新东西**，
才触发一次合并，让模型去重、用新的覆盖旧的、删掉没用的。
（合并跟着写入走，不单独扫盘 —— 没写就没必要重算。）
这一步是**破坏性**的（先删光再重写），所以：

```python
snapshot = {文件名: 原文}          # 动手之前先全存下来
try:
    删光 → 重写 → 重建索引
except Exception:
    删光 → 从 snapshot 按字节还原 → 重建索引
    raise                          # 异常照旧抛出去，不吞
```

实测：制造一个 slug 冲突让合并中途失败，12 个文件**逐个字节还原**，无残留、索引完好。
输入超过 `CONSOLIDATE_INPUT_CHAR_LIMIT` 则直接拒绝，一个字都不动。

**第 6 步：召回 —— 目录全量，正文按需。**

```
SystemMessage = SYSTEM_PROMPT
              + RECALL_NOTE
              + Memory catalog:        ← 每条记录的 name + description，全量
              + Relevant memory records: ← 相关记录的完整正文，按需
```

目录是 `MEMORY.md` 原文，每轮都整个注入。正文再按当前请求挑几条 —— 挑的动作就是
下面那个 box 说的。

> **这一步的检索可以换成 RAG 检索。**
>
> 现在的实现是**一次模型调用**：把目录发给模型，让它返回相关条目在目录里的**下标**
> （`max_tokens=200`）。这是 LLM-as-retriever，不是 RAG 的检索器 —— 所以它慢，
> 而且慢在**每轮提问的关键路径上**，用户得等。
>
> 规模小的时候这笔开销不划算：目录（每条记录的 name + description）本来就每轮全量注入，
> 那次调用往返一趟，只为了决定"正文取哪几条"，而**全部正文加起来也就和目录一个量级**。
> 换成关键词或向量检索（都是毫秒级、零网络）能直接省掉这次往返；盘大了再换向量库，
> 调用方的接口不用动。
>
> 文件里已经有一个关键词版本 `keyword_memory_selection`，但它现在是 `except` 上的兜底，
> 而且**中文切词是坏的**：正则 `[一-鿿]{2,}` 会把整段连续中文当成一个词，
> 于是 `"你是谁"` 切出 `{"你是谁"}`，在目录里当然找不到，一条都选不出来。
> 英语恰好正常（`[a-z0-9_]{3,}` 按空白切好了）。
>
> 所以它现在算不上兜底 —— LLM 调用一失败，召回会**静默退化成"什么都不召回"**。
> 要拿它当默认，得先把切词改成 2-gram 滑窗。

**第 7 步：注入的东西得说清它是什么。**

一句话，压在两个标题前面（**只在真有东西可注入时才加** —— 空库时说"内容已经在这里了"是假的）：

```python
RECALL_NOTE = (
    "Below is long-term memory about the user, recalled from earlier sessions. "
    "The content is already included here — treat it as known background and "
    "do not read the memory files again unless it is genuinely necessary."
)
```

（模型侧的提示词全仓库都是英文，这句也照此。）

> **为什么需要这句话 —— 实测出来的。**
>
> 目录是 markdown 链接 `- [名字](文件名.md) - 描述`，记录是
> `{"source": "文件名.md", "content": "..."}`。两处都在用**文件的语言**。
>
> 于是问一句"你是谁"，模型的反应是挨个把文件打开：
>
> ```
> [HOOK] PreToolUse: read_file(['project_working_directory.md'])
> [HOOK] PreToolUse: read_file(['用户偏好-缩进风格.md'])
> [HOOK] PreToolUse: pwsh(['Get-ChildItem -Recurse -Depth 2 | ...'])
> ```
>
> 它读的正是目录里那两个链接的目标，一字不差。**全文其实已经注入在 records 里了** ——
> 但它不知道这件事。加这句就是告诉它。

**第 8 步：显式入口 —— 用户说"记住"就不该由启发式裁决。**

`/remember` 走的是另一条路：**不经过 `should_store_memory`**。

理由是那三道闸门（scope 门槛、临时字眼、去重）都是用来**替用户猜**"这条值不值得长期记住"的。
用户一旦明说"记住"，就没有可猜的了 —— 拿猜测器去否决一条指令是用错了权威。

实测同一句话走两条路：

```
/remember 记住：暂时用 tabs 缩进，以后再说
    命令行  → 落盘成功
    提取路径 → should_store_memory = False    ← 命中 "暂时"，静默丢弃
```

模型在这里只干一件事：**把一段自由文本拆成 `name` / `description` / `body`**，
去留由用户决定。

> **两个坑。**
>
> 一、`type` 不问模型。它在这一层已经定死是 `user`（用户亲手 `/remember` 的就是用户偏好），
> 让模型复述一个已经定死的常量，只会多一个出错的地方 —— 第一版就在这儿翻了车：
> 提示词只要 `name/description/body` 三个字段，而 `validate_memory_record` 要求四个，
> 于是**每一次 `/remember` 都必然失败**。
>
> 二、`body` 不能"逐字保留"。第一版提示词写的是 `body keeps the full detail verbatim`，
> 结果 description 是第三人称、body 是"我习惯用 4 空格缩进" —— **同一个文件里两种人称**。
> 而这条记录将来是被塞进**另一个会话的系统提示词**的，那里的"我"最自然会被读成**助手**自己。
> 现在改成要求第三人称、自足（把"这个""上面说的"展开）、但具体信息（路径、名字、版本）不许洗掉。

**第 9 步：命令层。**

`/compact`、`/remember`、`/memories` 统一挂在顶层的 `commands.py`：

```python
register("/remember", remember)      # 命令层自带的
register("/memories", memories)
register("/compact", COMPACTION.compact_now)   # 组装点挂上来的
```

**注册只有一个入口。** `/compact` 得从外面挂，是因为压缩器是运行时才建出来的**对象**，
它的处理函数是个绑定方法 —— 命令层不 `import` 中间件，谁建的谁挂。

认不出的 `/` 命令只提示、不提问：错字不该变成一次真实的模型调用。

## 跑起来

```bash
uv run python s09_memory/main_loop.py
```

`WORKDIR` 取自 `__file__`，所以在仓库根启动也没问题。

### 手动命令

```
s09 >> /remember 我习惯用 4 空格缩进
[memory] 已记住 [缩进偏好：4 空格] -> 缩进偏好-4-空格.md
         用户习惯用 4 个空格缩进，不用 tab。

s09 >> /memories
[memory] 1 条：
  [user] 缩进偏好：4 空格 — 用户习惯用 4 个空格缩进，不用 tab。
```

`/memories` 从记录现场读，不走索引文件 —— 免得索引过期时看到的和实际对不上。

### 看哪里

```bash
cat s09_memory/.memory/MEMORY.md            # 索引：每题一行 name + description
cat s09_memory/.memory/<名字>.md            # 每条记录：YAML frontmatter + 正文
```

`.memory/` 是运行产物，已在 `.gitignore` 里。

**每个钩子喊一声。** 终端上每条 `[HOOK] <事件名>: <内容>` 就是一次触发：

```
[HOOK] UserPromptSubmit: working in D:\...\s09_memory
[HOOK] PreAgent: recalling memories
[HOOK] PreToolUse: permission gate read_file
[HOOK] PreToolUse: read_file(['config.py'])
[HOOK] Stop: session used 1 tool calls
[HOOK] PostAgent: storing memories
```

### 每轮要调几次模型

这是这一章的性能账，值得摊开：

| 时机 | 调用 | 备注 |
| --- | --- | --- |
| `PreAgent` | 选相关记录 | **在关键路径上**，用户要等 |
| `PostAgent` | 提炼新记录 | 答案已经拿到，但打印要等它返回 |
| `PostAgent` | 合并 | 只在攒够 10 条时 |

记忆系统不是免费的。写路径的那两次尤其值得换掉 —— 见第 6 步那个 box。

## 目录结构的变化

```
s09_memory/
├── config.py
├── commands.py                 ★ 命令层：注册表 + 分发（本章新增）
├── hooks/
│   ├── memory_hook/            ★ 记忆子系统（本章新增）
│   │   ├── memory_store.py     读写 + 索引 + 去重 + 路径守卫
│   │   ├── recall_memory.py    PreAgent：选 + 注入
│   │   ├── extract_memory.py   PostAgent：提炼 + 合并
│   │   └── memory_commands.py  /remember、/memories
│   ├── trigger_hooks.py        ★ 多了 PreAgent / PostAgent 两个事件
│   ├── other_hooks.py          与 s08 相同
│   └── permission_hook.py      与 s08 相同
├── middleware/compaction/      与 s08 相同
├── tools/
├── utils/
├── skills/
└── main_loop.py                ★ agent_loop 只多两行
```

**记忆是钩子，不是中间件。** s08 的压缩包在模型外面，因为它要**改 `messages`**；
记忆只在两个确定的时刻读一次、写一次，没有"每次调模型前都过一遍"的需求，
所以挂在事件上更贴切。这也让 `main_loop.py` 的改动比 s08 还小。

**子包自足。** `memory_commands.py` 里那句 `type` 定死、`memory_store.py` 里的路径守卫，
都不认识 `main_loop.py`；`commands.py` 也不认识中间件。谁建的谁挂。

---

## 阶段回顾

从 s01 到现在，主循环**一次都没有改过结构**：

```python
while True:
    response = model_with_tools.invoke(messages)
    messages.append(response)
    if not response.tool_calls:
        return
    for tool_call in response.tool_calls:
        output = ...执行...
        messages.append(ToolMessage(...))
```

后面每一章加的东西，全都是围绕这个循环、而不是改动它：

| 章节 | 加在哪里 | 加的是什么 |
| --- | --- | --- |
| s01 | 循环本身 | 一个循环 + 一个工具 |
| s02 | 循环里 | 更多工具（循环结构不动） |
| s03 | 循环里 | 执行前的准入判断 |
| s04 | 循环外 | 钩子，把准入判断搬出去 |
| s05 | 循环里 | 任务状态 + 催促 |
| s06 | 循环里 | 一次调用背后跑另一个完整循环 |
| s07 | 循环外 | 系统提示的内容策略 |
| s08 | 循环外 | 在模型外面包一层，改掉它看到的 `messages` |
| s09 | 循环两端 | 两个钩子事件：进前召回，出后提炼 |
| … | | 这张表会随章节继续往下长 |

s09 是**第一次复用已有机制、不引入新机制**的章节 —— 没有新的层、没有新的协议，
只是在 s04 的事件表上多了两个事件。`agent_loop` 33 行 → 35 行。

**前八章攒下的东西，这一章开始还债了。**

---

## 下一步

记忆解决了"跨会话记得住"，但记的是**知识**。有一类东西它管不了：**任务**。

"这个 bug 修到一半"、"第三步等用户确认"、"剩下的明天接着做" —— 这些是**状态**，
不是事实。它们有依赖关系、有完成与否、需要在被打断之后原样恢复，
而且**不能靠模型判断该不该记**。

s05 的 `todo_writer` 是个开始，但它是模型自己管的、活在上下文里、会话一结束就没了。

→ s10 Task System：把任务从上下文里拿出来，变成一个可以落盘、有依赖、能恢复的对象？
