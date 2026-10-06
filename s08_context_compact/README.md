# s08: ContextCompact — 按远近分区，越旧压得越狠

[s01](../s01_loop/) → [s02](../s02_tools/) → [s03](../s03_permission/) → [s04](../s04_hooks/) → [s05](../s05_todo_writer/) → [s06](../s06_subagent/) → [s07](../s07_skill_loader/) → `s08` → s09 → s10 → s11 → s12 → s13 → s14 → s15 → s16 → s17

> *「不是删掉历史，是让越旧的东西越模糊 —— 而原文一直都在磁盘上」*

---

## 问题

前面七章一路加下来，有个问题一直没正面处理：**上下文只增不减。**

每一轮的工具输出都永久留在 `messages` 里。一次 `read_file` 拉进 500 行，它就再也不会离开。
跑几十轮之后，历史里塞满早已无用的中间结果：

- 每一轮 API 调用都要**重新发送**全部历史
- 窗口被挤占，真正重要的信息反而被挤出去
- 模型的注意力被稀释

s06 的子代理是对这个问题的**局部**缓解（把一趟探索隔离出去），但主对话本身仍然只涨不缩。

而"压"这件事，一旦动手就会碰到两个不能破的约束：

1. **带 `tool_calls` 的 AI 和它的 `ToolMessage` 必须成对。** 切断了，下一次 API 调用直接报错。
2. **开头的系统提示不能动。** 它是 prompt cache 的稳定前缀，也是 agent 的行为约束。

## 方案

> **上下文按「离现在多远」分成三段，越远处理得越狠；任何被替换的东西，原文先落盘。**

每次调模型之前，把消息列表切成三段：

```
messages = [  frozen  ][      middle      ][    recent    ]
              System 前缀    较早的对话        最近 3 轮
              零改动         指针 / 摘要        只预览
```

| 区 | 是什么 | 怎么处理 |
| --- | --- | --- |
| `frozen` | 开头连续的 SystemMessage | **一个字都不动** |
| `middle` | 中间的全部 | 指针，不够就整段摘要 |
| `recent` | 最近 3 轮 | 只在万不得已时预览 |

三种处理手段，按损失从轻到重：

| 处理 | 上下文里留下 | 原文去向 | 损失 |
| --- | --- | --- | --- |
| **预览** | 原文前 4,000 token + 路径 | `.tool-results/` | 小 |
| **指针** | 只剩路径 | `.tool-results/` | 大（单条全丢） |
| **摘要** | 一段提炼文本 | `.transcripts/` | 最大（整段重写） |

前两种是纯字符串替换，**不花模型调用**；只有摘要要调一次模型。

### 核心原理：损失沿时间轴单调递增

```
frozen（零损失） → recent（预览） → middle（指针） → middle（摘要）
   越新  ←──────────────────────────────────────────────→  越旧
```

**越旧的信息，越舍得丢。** 这是整条管线的正确性依据。反过来说，把"指针"用在
`recent` 上就是错的 —— 模型会凭空丢掉刚拿到的工具结果，而且不报错，只是开始
基于不存在的观察往下推理。

## 代码怎么走

**第 1 步：先解决"怎么知道超了"。**

最自然的写法是按字符数估：

```python
def estimate(ctx): return sum(len(m.content) for m in ctx)
```

实测下来这个估计器**不能用**。拿它和 API 回报的 `input_tokens` 比，比值在
**0.66 ~ 1.58** 之间 —— 一个字符顶的 token 数，日志/路径/数字类内容能是散文的两倍多。

危险的方向是**低估**（以为没超、其实超了），最坏低估 **1.52×**。

换成 DeepSeek 官方词表之后：

| 估计器 | 估/真 区间 | 最坏**低估** |
| --- | --- | --- |
| 字符启发式 | 0.66 ~ 1.58 | **1.52×** |
| tiktoken `o200k_base` | 0.99 ~ 1.15 | 1.01× |
| **官方词表** | **1.000** | **1.00×** |

```python
# middleware/compaction/tokenizer.py
@lru_cache(maxsize=_COUNT_CACHE_SIZE)
def _count(text: str) -> int:
    return len(_get().encode(text, add_special_tokens=False).ids)

def get_token(text: str) -> int:
    return _count(text or "")
```

**词表不是代码。** 7.8MB 的 `tokenizer.json` 不进版本库：文件不在就自动拉一次到
`<仓库根>/.models/`（已 gitignore），落到磁盘之后就再也不联网。

拉下来先按 **sha256 校验身份**才用。这一步不是多余的 —— 公开的是 V3 那份词表，
未必等于 `deepseek-flash` 实际用的那份。判据是**差额恒定**而非随长度成比例：

```
长度扫描  2k / 5k / 10k / 20k / 40k   →  与 API 差额  30 / 30 / 30 / 30 / 30
内容类型  英文 / 中文 / 混排 / 源码 / 日志数字 / CJK  →  差额恒为 30
```

> **踩到的坑：`urllib` 的默认 UA 会被 hf-mirror 回 403。**
> 默认是 `Python-urllib/3.12`，而 `curl` 能下 —— "顺手用 `urlretrieve` 拉一下"
> 这条路径看起来天经地义，实际是坏的，**不真跑一次发现不了**。现在显式带 UA。

**第 2 步：窗口大小是量出来的，不是从模型名推的。**

别名的 `[1M]` 只是个名字。发一个**故意超限**的请求，错误信息会直接报出真数字：

```
This model's maximum context length is 1048576 tokens.
However, you requested 1500034 tokens (1500034 in the messages, 0 in the completion).
```

**1,048,576**（1 Mi，不是整 1,000,000）。

这同一个报错还顺带验证了词表 —— 本地构造 1,500,001，API 数出 1,500,034，差 **33**，
正是下面那个常量。**150 万 token 上误差 0.002%。**

```python
# middleware/compaction/estimate.py
REQUEST_OVERHEAD = 30        # chat 模板 + 角色标记，是**每次请求**的常量
```

**它是每次请求的常量，不是每条消息的。** 实测 2/3/5/10/20 条消息上分别是
30/31/33/30/30 —— 想当然地写成 `每条 × 条数` 就错了。

还有两处**已知不含**（都不修，但要知道）：

- **工具 schema 不在里面。** 绑 8 个工具实测固定占 861 token，那部分挂在
  `bind_tools` 上、不在 `messages` 里。它是常量，只是把预算整体平移 1.4%。
- **缓存字段的口径，两个 provider 是反的。** Claude Code 里 `input_tokens` 不含缓存读，
  所以要把三个字段相加；**DeepSeek 的本身就含**，照抄那行公式会重复计数。

**第 3 步：切点安全由一个谓词保证。**

```python
def is_safe_cut(messages, i: int) -> bool:
    """切在 i 处是否安全：切点后面不能紧跟 ToolMessage。"""
    return i == 0 or not isinstance(messages[i], ToolMessage)
```

只需要这一条：消息列表里唯一要成对出现的就是 `AI(tool_calls)` 和它的 `ToolMessage`，
而工具组在列表里的形状固定是「一条 AI + 紧随其后的若干条 ToolMessage」，
所以任何一条 `ToolMessage` 的发起者都在它**之前**。

> **一个容易写错的版本**：用「前一条不是带 `tool_calls` 的 AI」作判据。
> 它在一次只调一个工具时是对的，但模型**一次可以发多个 tool_calls**：
>
> ```
> AI(tool_calls=[a, b]), Tool(a), Tool(b)
> ```
>
> 切在 `Tool(b)` 前时，前一条是 `Tool(a)`，那个判据会放行 —— 但 `Tool(b)` 的发起 AI
> 已经留在切点之前了。实测确认过 DeepSeek 会一次返回 2 个 tool_calls，这不是理论情形。

轮边界（真实 `HumanMessage` 的位置）**天然满足**这个谓词，所以切点优先取轮边界，
不够用时才往轮内推。这样"保留最近三轮"里的"三轮"才有意义。

**第 4 步：所有阈值都是比例，只有一个锚点。**

```python
TOKEN_BUDGET      = 800_000  # 唯一锚点。实测窗口 1,048,576，取 76%。

TARGET_RATIO      = 0.65     # 目标：压到 BUDGET × 0.65
WARN_RATIO        = 0.80     # 预警线，纯渲染量
RECENT_RATIO      = 0.50     # recent 区的配额
RECENT_TURNS      = 3        # 轮数是语义量，**不随预算缩放**

PREVIEW_RATIO       = 0.005  # 预览保留量      → 4,000 tok
PERSIST_MIN_RATIO   = 0.02   # 低于此值不落盘  → 16,000 tok
SINGLE_RESULT_RATIO = 0.05   # 单条硬上限      → 40,000 tok
USER_MESSAGES_RATIO = 0.13   # 原话的预算      → 104,000 tok
```

**为什么全改成比例。** 这套管线的机制与窗口大小无关 —— 所以窗口变了（128k → 1M）、
模型换了，应该只动一个数字。`RECENT_TURNS` 是唯一的例外，而且它**不该**跟着缩放：
"最近三轮"是语义量，不是量纲，预算翻十倍仍然是三轮。

上一版这里是 `PREVIEW_CHARS = 1_000` / `PERSIST_MIN_CHARS = 4_000` 这样的字符常量，
带来两个毛病：单位混用，以及"它必须显著小于预算，否则落盘这一级静默跳过"这条警告
**要靠人记住**。改成比例之后，这条由常量的**形式**保证 —— 写不出"接近预算"来。

**锚点必须明显低于窗口。** 因为"触发时"到"请求发出时"之间还会长：

```
TOKEN_BUDGET + 两次 prepare 之间的增长（一个工具批次） + 模型的输出预留 < 1,048,576
```

所以"把锚点提到等于窗口"是自相矛盾的 —— 写到 1,048,576，压缩会在请求已经被 API
拒掉之后才想动手。

**第 5 步：管线。三个步骤，代价递增。**

```python
def prepare(self, messages, active_request, focus=None, force=False):
    frozen, rest = split_prefix(messages)

    # 前置：单条超大的工具结果，无条件先缩（不看预算，见第 6 步）
    report = Report(pre=estimate_tokens(messages))
    self.cap_oversized_results(rest, report)

    used = estimate_tokens(messages)        # cap 是就地改内容的，必须重新量
    if not force and used <= TOKEN_BUDGET:
        return messages                     # 未触发，零改动

    target = int(TOKEN_BUDGET * TARGET_RATIO)
    middle, recent = split_by_budget(rest, int(TOKEN_BUDGET * RECENT_RATIO))

    # 步骤 1：榨干 middle —— 指针，作用于较旧的一侧
    middle = self.pointerize_tool_results(...)
    if estimate_tokens(frozen + middle + recent) <= target:
        return self.rebuild(frozen, middle, recent, report)

    # 步骤 2：仍超预算 → 才动 recent —— 预览，**默认什么都不做**
    recent = self.preview_tool_results(...)
    if estimate_tokens(frozen + middle + recent) <= target:
        return self.rebuild(frozen, middle, recent, report)

    # 步骤 3：middle 整段 → 摘要（最大损失，唯一一次模型调用）
    if middle:
        middle = [self.summarize(middle, active_request, focus, report)]
    return self.rebuild(frozen, middle, recent, report)
```

几处不显然的地方：

- **顺序是刻意的。** 先用重处理榨干较旧的 `middle`，不够才用轻处理碰较新的 `recent`。
  反过来就等于"刚拿到手的结果先被砍，而更早的还留着全文"。
- **进入与目标分离。** 进入条件是 `> TOKEN_BUDGET`（800k），达标条件是 `≤ target`（520k）。
  这 280k 的差额就是**迟滞量** —— 否则压完刚好贴着触发线，下一轮立刻又触发。
- **`middle` 里按体积降序处理，达标后立即停止。** `middle` 整体已经比 `recent` 旧，
  "年龄"这一维由分区保证了，剩下的判据就是体积。
- **步骤 2 的默认值是"什么都不做"。** 它是整条管线里唯一会削弱"模型刚拿到的信息"的动作，
  所以排在所有确定性手段之后、摘要之前。
- **`if middle:` 这个判断不能省。** 否则 `middle` 为空时每轮都白花一次模型调用。

**第 6 步：单条超大的硬上限 —— 它不看预算。**

前五步都是"总量超预算之后怎么压"。还有另一种危险：**一条工具结果自己就能撑爆窗口。**

`read_file` 没有大小上限。读一个 4MB 的文本文件约 **100 万 token** —— 那时总量还没到
触发线，但请求已经发不出去了。所以这一条**无条件执行**，位置在触发判断**之前**：

```python
SINGLE_RESULT_TOKENS = int(TOKEN_BUDGET * SINGLE_RESULT_RATIO)   # 40,000
```

放到触发线后面就等于没加 —— 那条超大结果正是让 `used` 冲过窗口的东西，
而 `used <= TOKEN_BUDGET` 会先把函数 `return` 掉。

它换成的是**预览**而不是指针：指针不许用在 `recent` 上（见"核心原理"），而这一条会
碰到最新的那条结果。预览保留开头、原文一跳可达，所以它只破"单调递增"这一个断言。

**第 7 步：落盘。两个目录，职责不同。**

| 存储 | 内容 | 谁读 | 生命周期 |
| --- | --- | --- | --- |
| `.tool-results/<tool_call_id>.txt` | 被指针化/预览的结果**原文** | **模型**（按指针 `read_file`） | 会话内，**不得清理** |
| `.transcripts/session_<id>.jsonl` | 离开上下文的**全部**消息，原样 | **人 / 工具**（恢复用） | 永久 |

归档的唯一规则：**原样写入，不解析、不跳过、不改编。** 追加模式，一会话一文件。

路径记在**实例字段**上，不解析标记 —— 摘要会把标记整个换掉，而且标记里的路径是
"给模型看的字符串"，代码依赖它等于把提示当成了数据结构。

> **一个必须显式写出来的守卫：禁止双重包裹。**
>
> 如果 `.tool-results/` 里的文件被手工删掉，`persisted_output_path` 会返回 `None`，
> 代码就会把**指针文本本身当成"原文"重新落盘**，产出一个指向指针的新指针。
>
> 长度守卫碰巧能挡住它（指针串只有几十个 token，远低于落盘门槛），但那是**长度巧合
> 不是设计保证** —— 一旦调整那两个比例，防线就没了。

**第 8 步：摘要消息的版式由代码装配，不发给任何模型。**

```
[Compacted]  est. 812,340 -> 498,110 tok · 42 messages archived · transcript: session_7f3a.jsonl

Current user request:
<原话，一字不改>

All user messages (verbatim, oldest first):
1. <原话>
（中间省略 12 条）
19. <原话>

Current plan:
[>] 读取 config.py 找到 PS7_PATH
[ ] 改成从环境变量读取
(0/2 completed)

Conversation summary (reference only):
1. Goal        - ...
...
8. Gotchas     - ...

Full transcript: <绝对路径>
```

六行里只有 `Conversation summary` 一行来自摘要模型，其余全是代码拼的。

**逐条保留用户原话，不转述。** 摘要会漂 —— 压几轮之后"用户到底要什么"可能就偏了，
而且**没人察觉**。原话是防漂移最便宜的手段。

**`Current plan:` 是 s05 状态的重注入。** Claude Code 压缩后会把 CLAUDE.md 重新注入，
因为它在稳定前缀里，天然不受摘要影响。而 s05 的 todo 列表是**运行时状态**，
它活在历史的消息里，`middle` 一摘要就没了。

> 普遍陷阱：**上下文里承载的状态，会被压缩吃掉。** 状态应该放在前缀或外部。

**第 9 步：注入，而不是写进循环。**

```python
COMPACTION = build_compaction(model)
model_with_tools = inject(model.bind_tools(TOOLS), COMPACTION)
```

然后循环里还是原来那句 —— **和 s07 一字不差**：

```python
response = model_with_tools.invoke(messages)
```

注入之后 `model_with_tools` 是一个 `Chain`，长得和模型一样。中间件协议只有一个方法：

```python
def around(self, messages, call_next) -> AIMessage
```

`call_next(messages)` 把控制权交给内层。中间件同时拿得到两样东西：请求（`messages`）
和响应（`call_next` 的返回值）—— 这正好够压缩用，它既要在发送前改 `messages`，
又要在**报错后**重试。

**"必须就地改 `messages`"这条约束在这里，而不是在 `main_loop.py`：**

```python
messages[:] = self.compactor.prepare(messages, active_request_text(messages))
```

`prepare` 返回新列表；把它赋给本地的 `messages`（而不是切片赋值），调用方的历史
就会悄悄和压缩结果脱钩 —— 循环继续往**旧列表**追加消息，压缩白做，**而且不报错**。

类型系统抓不住这个错（两种写法都合法），所以碰 `prepare` 返回值的地方被收在
**一个文件**里 —— `wiring.py` 里只有 `around` 和 `handle_command` 两处，都是切片赋值。
实测这一条 —— 注入后调用方手里那个列表从 **90,068 → 4,126 tok**。

## 跑起来

```bash
uv run python s08_context_compact/main_loop.py
```

### 默认锚点撞不到，但调小之后真的能跑

真实锚点是 `TOKEN_BUDGET = 800_000`，而这一章的全部 24 个 `.py` 加起来才 **19,815 token**
（已含系统提示与工具调用开销）。**把整章读一遍也够不到触发线**，日志一行都不会有。
800k 是给长会话留的，不是给小实验看的。

把锚点调小（比如 `30_000`）之后，真实会话**能跑出完整管线**。但有一个前提：

> **工具结果要「多而中等」，不能「少而巨大」。**

单条上限是预算的 5% —— 一条 6,242 token 的结果，在 30k 预算下（上限 1,500）会**先被单条上限截住**，
轮不到后面的指针和摘要。两种读法的实测差别：

| 读法 | 单条体积 | 结果 |
| --- | --- | --- |
| 整文件 `read_file` | 最大 6,242 tok | 几乎全落在「单条上限」这一级 |
| `pwsh` 分片（`(Get-Content $f)[30..110]`） | 几百 tok | 指针 / 预览 / 摘要全都跑得到 |

真实会话里模型自己会选后者 —— 大文件 `read_file` 只回预览 + 指针，它就改用 `pwsh` 切片读。
于是**上限逼着读取变碎，碎读取才喂得出主管线**：这两件事是配套的。

本章的每一级处理也都有行为断言覆盖（`frozen` 逐字节不变、工具配对完整、归档可往返……），
那些断言跑的时候用小锚点，不然同样撞不到。

### 三种日志行

代码里一共只打这三种：

```
[compact:<trigger>] <前> -> <后> tok | oversized: <n> tool result -> preview | middle: <n> tool result -> pointer, <n> msg -> summary | recent: <n> tool result -> preview
[compact:<trigger>] transcript: <归档文件的绝对路径>
[budget] <当前> / <触发线> tok (<百分比>%) — nothing compacted
```

`<trigger>` 取 `proactive`（每次 `invoke` 前）/ `reactive`（API 报超限后）/ `manual`（`/compact`）。

- **第一行**：结算单。`前 -> 后` 是一个**转移**，只在真的动手后打。后面每个字段**都带区名**，
  没生效的那一级**直接不出现** —— 一眼看出这次是谁干的活：

  ```
  [compact:proactive] 104,336 -> 21,006 tok | oversized: 1 tool result -> preview | middle: 2 tool result -> pointer, 17 msg -> summary | recent: 1 tool result -> preview
  ```

  `middle` 那一格是两个数并排：**先**有 2 条工具结果变成指针，**然后**整段 `middle`（17 条）
  又被摘要成一条。两步是先后关系，不写在一起就会看成"要么指针要么摘要"。

- **第二行**：归档路径。只在走到摘要（唯一一次模型调用）时才打。

- **第三行**：**预警线**（`WARN_RATIO = 0.80`）。前缀是 `[budget]` **不是** `[compact]`，
  形状也是**分数**（`当前 / 触发线`）而不是**转移**（`前 -> 后`），末尾还明写 `nothing compacted`
  —— 它**什么都没压**，只是个油表。

  它**只打印，绝不进消息流**：一旦发给模型，"压多少、留多少"就开始取决于模型听不听话，
  而本管线的前提是这些只由位置和预算决定。

### 手动触发

```
s08 >> /compact 重点保留分区规则那部分
```

`/compact` 走的是**同一条管线**，只是跳过触发线检查（`force=True`）。但它**不会
凭空造出内容来压**：如果对话本来就低于目标值（`TOKEN_BUDGET × 0.65`），它会什么都不做 ——
**手动不等于必须动刀**。

输入以 `/` 开头但认不出来时，**只打一句提示，不往下传给模型**：

```
s08 >> /comapct
未知命令：/comapct（只有 /compact [focus]）
```

**为什么不让它退化成普通提问**：错字本来会变成一次真实的模型调用 —— 模型看到 `/compact`
这个词，会照着编一份「Conversation Compact」报告出来，而上下文一个字都没动。
命令层的错字就该停在命令层。

匹配前先做 `NFKC` 归一化：中文输入法打出的 `／compact`（全角斜杠）和 `/compact`
看着一样、字节不同，不折就漏。命令名要求**整词**匹配，`/compactx` 不算 `/compact`。

### 看哪里

```bash
ls s08_context_compact/.transcripts/     # 归档：离开上下文的全部消息，原样
ls s08_context_compact/.tool-results/    # 工具结果原文，模型按指针 read_file
```

即便只触发到「单条上限」这一级，`.tool-results/` 里也会多出那条被 cap 的原文，
而上下文里那条 `ToolMessage` 已经换成「前 `PREVIEW_TOKENS` 个 token + 路径」的预览
（默认预算下 `PREVIEW_TOKENS` 是 4,000）。

## 目录结构的变化

```
s08_context_compact/
├── config.py                   ★ 多了 TRANSCRIPT_DIR / TOOL_RESULTS_DIR
├── middleware/                 ★ 中间件层（本章新增）
│   ├── __init__.py             组装点：Chain / inject / build_compaction
│   └── compaction/             压缩 —— 这里的**一个**中间件
│       ├── wiring.py           接线：谁在什么时候调它
│       ├── context_compact.py  分区 + 配额 + 管线
│       ├── archive.py          两个存储 + 标记 + 指针
│       ├── tokenizer.py        官方词表的懒加载 + get_token
│       └── estimate.py         消息 → token
├── tools/                      与 s07 相同
├── utils/
├── hooks/
├── skills/
└── main_loop.py                ★ 只多了两行：建件 + 注入
```

**件不认识 `config.py`。** `Compaction(model, transcript_dir, tool_results_dir)` 的
两个目录是**必填**的 —— 一个件不该知道自己在哪个项目里被装起来。于是
`middleware/compaction/` 整个子包是自足的，挪到别的项目能直接用；"归档放哪"这件事
全项目只在一处出现（`middleware/__init__.py` 的 `build_compaction()`）。

**为什么压缩要埋在子包里。** 它是本章的主角，但不是中间件层的全部 —— 重试、日志、
权限将来都会落在这儿。所以层与件分开：主循环只 `from middleware import ...`，
不该知道压缩内部拆了几个模块。

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
| … | | 这张表会随章节继续往下长 |

s08 是第一次**完全不碰循环、也不碰循环里的任何一行** —— 压缩被注入在模型外层，
循环里那句 `model_with_tools.invoke(messages)` 和 s07 逐字节相同（用 AST 比对确认过：
33 行，0 处差异）。

**循环是骨架，机制是挂件。** 这大概是到目前为止最值得带走的一条。

---

## 下一步

s08 解决的是「历史太长」。但被压掉的东西，**就真的只是被丢在磁盘上了** ——
`.transcripts/` 里躺着一份完整记录，模型却不知道它存在，也不会主动去翻。

用户上一周说过的偏好、项目里约定俗成的做法、踩过一次的坑，这些在下一章又要重新解释一遍。
压缩解决的是"放不下"，没解决"记不住"。

→ s09 Memory：怎么从对话里提炼出**值得长期保留**的东西，并在下次会话里主动想起来？
