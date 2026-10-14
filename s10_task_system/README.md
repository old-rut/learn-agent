# s10: Task System — 从执行清单到可协调的任务状态

[s01](../s01_loop/) → [s02](../s02_tools/) → [s03](../s03_permission/) → [s04](../s04_hooks/) → [s05](../s05_todo_writer/) → [s06](../s06_subagent/) → [s07](../s07_skill_loader/) → [s08](../s08_context_compact/) → [s09](../s09_memory/) → `s10` → s11 → s12 → s13 → s14 → s15 → s16 → s17

> *"大目标拆成小任务，排好序，持久化。"* — 文件持久化的任务图，多 agent 协作的基础。
>
> **Harness 层**: 任务 — 持久化的目标，可恢复的进度。

---

## 问题

s09 的结尾埋了一句话：**任务是状态，不是事实。** 记忆系统记的是知识——用户偏好、项目约定，这些是"以后还成立的东西"。管不了的是另一类：**"这个 bug 修到一半"、"第三步等用户确认"**。它们有依赖、有归属、有完成与否，被打断之后要原样恢复。

s05 的 `todo_writer` 干的就是这件事——但只干了一半。清单里的每一项只有内容和状态，用来提醒模型接下来做什么。当一件事拆成「建表 → 写 API → 加测试」时，Harness 需要知道的不止这些：

1. **关系。** 表没建完就不能写 API，API 没定就不能加测试。`todo_writer` 能显示"写 API 未完成"，但没有人能据此判断它**能不能开始**——清单没有边。
2. **分工。** 哪一步自己做、哪一步交给 `subagent`、做完没有——清单不记归属，Harness 无从仲裁。
3. **寿命。** 清单活在 `messages` 里：会话结束就没了；更糟的是 s08 讲过的那个普遍陷阱——**上下文里承载的状态，会被压缩吃掉**。

所以这一章把 todo 整个换掉：**每个任务有独立的 ID 和状态，`blocked_by` 记录前置任务，`owner` 记录谁在做，全部落盘。**

## 方案

> **任务从上下文搬到磁盘：一任务一个 JSON 文件，两个动作（claim / complete）驱动三个状态，依赖构成一张有向无环图。**

```
用户提问
   ↓
agent_loop（和 s09 逐行同构 —— 还短了 12 行，见第 8 步）
   ↓
模型调 create_task / update_task / claim_task / complete_task
   ↓
TaskStore 校验 → 读写 .tasks/<task_id>.json
```

状态机只有两个动作、三个状态：

```
          claim_task                complete_task
pending ─────────────→ in_progress ─────────────→ completed
```

| | todo_writer (s05) | Task System (s10) |
| --- | --- | --- |
| 定位 | 当前任务的执行清单 | 可恢复的任务系统 |
| 存储 | 进程内的 `messages` | `.tasks/<task_id>.json` |
| 依赖 | 无 | `blocked_by` 依赖图（带环检测） |
| 分工 | 不记归属 | `owner` 认领 |
| 更新契约 | 整表替换 | 单条记录的建 / 读 / 改 / 列 |
| 压缩 | 会被吃掉（s08 陷阱） | 摘要装配时**从文件重新注入** |
| 生命周期 | 当前会话 | 跨会话恢复 |

### 核心原理：状态放在上下文外面

s08 埋的伏笔在这里兑现。**上下文里承载的状态会被压缩吃掉**——s05 的清单住在 `messages` 里，`middle` 一摘要就没了，所以 s08/s09 不得不在摘要装配时把它重新注入。s10 干脆换个地方住：

- 任务在 `.tasks/` 里，**压缩碰不到**——上下文怎么压，任务图完好无损；
- 摘要装配照样有 `Current plan:` 槽位，但这次不是从消息里抢救，而是**直接读文件**；
- 会话结束再开一个，`.tasks/` 还在，读文件就能接着干。

**上下文是工作内存，文件才是状态。** 这是任务系统和记忆系统共享的判断——s09 把知识放到了会话外面，s10 把状态放到了会话外面。

## 代码怎么走

**第 1 步：六个字段的最小集。**

```python
@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: str              # pending | in_progress | completed
    owner: str | None        # 谁认领了这条任务
    blocked_by: list[str]    # 前置任务的 ID 列表
```

| 字段 | 角色 | 谁写 |
| --- | --- | --- |
| `id` | 主键：文件名、被 `blocked_by` 引用、工具寻址 | 系统生成，模型只复述 |
| `subject` / `description` | 内容：一句标题 / 完整细节 | 模型（create_task） |
| `status` | 状态机 | claim / complete 两个动作 |
| `owner` | 认领锁 | claim 写入，complete 校验 |
| `blocked_by` | 依赖边（引用 ID，不是嵌套对象——所以能落盘） | update_task |

> **为什么 ID 这么短。**
>
> `task_` + 8 位十六进制（`secrets.token_hex(4)`），不是 UUID。这些 ID 的主要用户是**模型**：create_task 返回它，模型要在 update_task / claim_task / complete_task 里逐字复述——短的省 token，长串抄写出错率也高。43 亿种组合对几十条任务的规模绰绰有余；真撞了，`open("x")` 独占创建 + 重试 100 次兜底。`task_[0-9a-f]{8}` 这个格式本身还是防线：ID 里进不来 `/` 和 `..`，就构造不出逃出 `.tasks/` 的路径。

**第 2 步：TaskStore —— 校验、读写、守卫。**

```python
def _path(self, task_id: str, create_root: bool = False) -> Path:
    if not isinstance(task_id, str) or not TASK_ID_PATTERN.fullmatch(task_id):
        raise ValueError(f"Invalid task ID: {task_id!r}")
    root = self._root(create=create_root)
    path = (root / f"{task_id}.json").resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Invalid task ID: {task_id!r}")
    return path
```

读写的四条纪律：

- **ID 先过格式**——格式顺带挡路径穿越；
- **存储根目录必须落在 `WORKDIR` 里**，逃出去就拒绝；
- **创建用 `"x"` 模式**——文件已存在就抛 `FileExistsError`，唯一性闸门住在创建里；
- **读回时校验两件事**：文件名和记录里的 `id` 一致、`status` 在词表里——盘上的东西不可信。

**第 3 步：两阶段建图。**

任务图分两步搭：先创建所有节点，再拿返回的 ID 连边。

> **为什么不能一步到位。**
>
> 模型可以在一条回复里同时发出多个工具调用，而**同一批调用在任何一个结果产生之前就已经全部确定**——`create_task` 的参数里引用不了兄弟调用还没生成的 ID。所以提示词的工作流里明确教了这个顺序：
>
> ```text
> 1. Use `create_task` to create one task per concrete step. Create all task
>    nodes first, then use `update_task` with the returned IDs to declare
>    dependencies (what must finish before what).
> ```

**第 4 步：连边前的四道检查。**

`update_dependencies` 在保存前把整批依赖验完：

| 检查 | 失败时 |
| --- | --- |
| 自依赖 | `Task cannot depend on itself` |
| 依赖存在 | `Dependency not found: task_…` |
| 成环（沿 `blocked_by` 递归走） | `Dependency cycle detected: … -> …` |
| 重复添加 | **静默去重**——已有边不重复写 |

实测：

```
self   -> Task cannot depend on itself
cycle  -> Dependency cycle detected: task_e4846fa5 -> task_a8b22133
missing-> Dependency not found: task_00000000
同一条边加两次 -> ['task_e4846fa5']（仍是一条）
```

还有一个时间闸门：**依赖图只在 `pending` 且无人认领时可改。** 动工之后边就冻结了——给进行中的任务半路加依赖，状态机和依赖图就对不上了。

**第 5 步：claim / complete —— 两个动作。**

claim 是两道闸门，过完才写 owner：

```python
def claim_task(task_id: str, owner: str = "agent") -> str:
    task = load_task(task_id)
    if task.status != "pending":
        return f"Task {task_id} is {task.status}, cannot claim"
    dependencies = incomplete_dependencies(task)
    if dependencies:
        return f"Blocked by: {dependencies}"
    task.owner = owner
    task.status = "in_progress"
    TASKS.save(task)
    ...
```

complete 除了状态闸门还查归属——**别人认领的任务你不能替他完成**；并且只报告**刚刚**被解锁的下游：`ready_before` 记下完成前就能开工的任务，完成后只报差集，之前就绪的不算"新解锁"。

实测（四节点：schema → endpoints → tests，docs ← schema）：

```
Claimed task_39f6c7c2 (design schema)
Completed task_39f6c7c2 (design schema)
Unblocked: implement endpoints, write docs      ← 扇出：两个下游同时解锁

Claimed task_5acf693b (implement endpoints)
Unblocked: write tests                          ← 链式：一层一层往下走

[ ] task_26f760e1: write tests [pending] (blocked_by: task_5acf693b)
[x] task_39f6c7c2: design schema [completed] [agent]
[x] task_5acf693b: implement endpoints [completed] [agent] (blocked_by: task_39f6c7c2)
[ ] task_ef644da7: write docs [pending] (blocked_by: task_39f6c7c2)
```

认领被拒（依赖未完成）时**只返回原因，不落盘、不打日志**——拒绝不是状态变更。

**第 6 步：工具壳 —— 名字是给模型的。**

```python
@tool("create_task")
def run_create_task(subject: str, description: str = "") -> str:
    """Create a task and return its runtime-generated ID."""
    task = create_task(subject, description)
    ...
```

`@tool` 默认拿函数名当工具名，模型会看到 `run_create_task`。这里显式传短名：**工具名是模型看到的名字，函数名是实现细节**。SYSTEM_PROMPT 里的工作流教的就是短名，两边必须逐字一致——提示词指着一个不存在的名字说话，和 s09 那次"提示词只要三个字段、校验要四个"是同一类病。

> **踩到的坑：@tool 对象不能当函数调。**
>
> 压缩的摘要装配要把任务列表重新注入，最初直接写了 `run_list_tasks()`——实测炸了：
>
> ```
> TypeError: 'StructuredTool' object is not callable
> ```
>
> `@tool` 之后它就不再是函数。模型侧没事（`execute_tool` 走 `.invoke(args)`），内部调用方却踩空。修法不是让中间件学 `.invoke({})`——那是把工具协议的知识塞给不需要它的人——而是把渲染抽成裸函数：
>
> ```python
> def render_task_list() -> str:        # 裸逻辑，内部调用方用这个
>     ...
>
> @tool("list_tasks")
> def run_list_tasks() -> str:          # 薄壳，模型用这个
>     """List tasks with status, owner, and dependencies."""
>     return render_task_list()
> ```

**第 7 步：压缩时重新注入 —— s08 的槽位，s10 的数据源。**

s08 的摘要装配里有个 `Current plan:` 槽位，专门放"压不掉、就重新注入"的状态。s10 换了数据源：

```python
def _current_plan():
    try:
        from tools.task_system import render_task_list
    except Exception:                       # 单独用 estimate/archive 时不强依赖 s10
        return None
    return render_task_list()
```

没建过任务时渲染成 `No tasks. Use create_task to add some.`——让读摘要的人明确知道没有计划，而不是留一个空标题。

和 s08 的差别值得点破：那时候 `Current plan:` 是从消息里**抢救**出来的（todo 活在上下文里，摘要前不捞就没了）；现在是从文件里**读**出来的——状态本来就在上下文外面，压缩对它无能为力。

**第 8 步：主循环变短了。**

s05 在 `agent_loop` 里埋了段催促：三轮没动清单就注入一条 `<reminder>Update your todos.</reminder>`。s10 把它**整块删了**：

```text
agent_loop:  s09 的 35 行 → 23 行
```

不是"忘了写催促"，是**催促的职责消失了**。它原来提醒的是"去更新清单"；现在清单就是任务本身，推进由两边的闸门保证——工具侧，claim 拒绝未就绪的任务；提示词侧，工作流写明了 claim / complete 的时机。循环里不再需要任何人盯着。

**主循环从 s01 到 s10 一次都没改过结构，这是第一次变短——不是加机制，是把一段机制从循环里搬了出去。**

## 跑起来

```bash
uv run python s10_task_system/main_loop.py
```

试试这些 prompt：

1. `创建任务：设计数据库 schema、实现 API（依赖 schema）、写测试（依赖 API）、写文档（依赖 schema），先建节点再连依赖`
2. `列出所有任务和状态`
3. `认领第一个没有被阻塞的任务，完成后告诉我谁被解锁了`
4. `再列一次任务——哪些现在可以开始了？`

观察重点：`.tasks/` 下是否生成了 JSON 文件；完成任务后，被阻塞的任务是否出现在 `Unblocked:` 里。

### 看哪里

```bash
ls s10_task_system/.tasks/                # 一任务一个 JSON
cat s10_task_system/.tasks/task_<id>.json  # 纯 JSON：六个字段，indent=2
```

`.tasks/` 是运行产物，已在 `.gitignore` 里。终端上每次任务操作都有痕：

```
  [create] design schema
  [update] implement endpoints blocked_by: task_39f6c7c2
  [claim] design schema -> in_progress (owner: agent)
  [complete] design schema
  [unblocked] implement endpoints, write docs
```

## 目录结构的变化

```
s10_task_system/
├── config.py                   ★ 多了 TASKS_DIR（s09 的 MEMORY_DIR/MEMORY_INDEX 也一并挪进了 config）
├── commands.py                 与 s09 相同
├── hooks/                      与 s09 相同（memory 系统原样保留）
├── middleware/compaction/
│   └── context_compact.py      ★ Current plan: 槽位换数据源：todo → tasks
├── tools/
│   ├── task_system.py          ★ 本章新增：Task / TaskStore / 六个任务工具
│   ├── todo_writer.py          ✗ 删除——被 task_system 取代
│   ├── base_tools.py           六个基础工具，与 s09 相同
│   ├── subagent.py             与 s09 相同
│   └── ...                     file_tools / pwsh / skill_loader
├── utils/
│   ├── build_system_prompt.py  ★ MAIN_DELEGATION 换成任务工作流（建节点 → 连边 → claim → complete）
│   └── ...                     execute_tool / stream_print / extract_text
├── skills/
└── main_loop.py                ★ agent_loop 删掉催促：35 → 23 行
```

**工具的注册只有一张表。** `tools_register.py` 把六个任务工具和基础工具、subagent 并进同一个 `TOOLS`：

```python
task_system_tools = [run_create_task, run_update_task, run_claim_task,
                     run_list_tasks, run_get_task, run_complete_task]
TOOLS = BASE_TOOLS + [subagent] + task_system_tools
TOOLS_HANDERS = {t.name: t for t in TOOLS}
```

`t.name` 是装饰器传的短名（`create_task`…），和工具调用里的 `name` 字段直接对上——调度表不用单独维护一份映射。

**任务的知识只在两处。** 实现在 `task_system.py`，教法在 `build_system_prompt.py`；压缩中间件只认 `render_task_list` 一个裸函数，不认识任务长什么样。

---

## 阶段回顾

从 s01 到现在，主循环一次都没有改过结构：

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
| s10 | 文件系统 + 工具层 | 任务落盘成 DAG；循环里**删**掉 12 行 |
| … | | 这张表会随章节继续往下长 |

s05 那一行，这一章动了两处：**状态还在，住址变了**——从 `messages` 搬进文件，压缩和会话结束都伤不到它；**催促没了**——推进改由工具闸门和提示词工作流保证。

**s10 是第一章让主循环变短的章节。** 机制没有变多：todo 整个被任务系统替换，上下文里少了一个需要提醒、会被压缩吃掉的活物。

---

## 下一步

任务图能表达"什么等什么"了，但执行还是同步的：`pwsh` 里跑全量测试、装依赖，可能要几分钟——`agent_loop` 停在那个工具调用上干等，期间什么也做不了。

→ s11 Background Tasks：把慢操作放到后台。Agent 继续处理别的任务，后台跑完再接通知。
