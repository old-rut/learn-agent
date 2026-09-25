# s07: SkillLoader — 目录常驻，正文按需

[s01](../s01_loop/) → [s02](../s02_tools/) → [s03](../s03_permission/) → [s04](../s04_hooks/) → [s05](../s05_todo_writer/) → [s06](../s06_subagent/) → `s07` → s08 → s09 → s10 → s11 → s12 → s13 → s14 → s15 → s16 → s17

> *「提示词不是写得越多越好，是让模型知道有什么、需要时再拿」*

---

## 问题

s06 之后 agent 的能力够了，但**怎么把这些能力告诉模型**成了新问题。

写一段「怎么审代码」的详细说明，大概长这样：

```markdown
## 代码审查
只报会挂的缺陷。范围包括：语法错误、调用不存在的函数、类型不匹配、
空实现（pass / ... / NotImplementedError）。不报风格、命名、性能优化建议。
流程：先通读全部文件，再逐行判断；断言名字未定义前先全仓 grep……
输出格式：<file>:<line>  <what breaks> / evidence: <命令和输出>
```

两百来个字。加十个技能就是两千字 —— **每一轮 API 调用都要重复发送这两千字**，
而你十个技能可能只用到其中一个。

反过来，如果不写进系统提示，模型压根不知道有「代码审查」这个技能存在，你让它审代码它就自由发挥。

**要么每轮都付出全额成本，要么模型完全不知道。** 没有中间态。

## 方案

把每个技能拆成两半：

| | 内容 | 什么时候进上下文 | 成本 |
| --- | --- | --- | --- |
| **目录（catalog）** | 每行「技能名 + 一句话描述」 | 常驻系统提示 | 每个技能约 15 个 token |
| **正文（body）** | 完整的 SKILL.md | 模型主动调 `load_skill` 时 | 用到才付 |

```
系统提示里常驻的：                   磁盘上放着的：
Skills available:                    skills/code-review/SKILL.md
- code-review: Review code against   skills/pdf/SKILL.md
  a fixed narrow scope...            skills/whatever/SKILL.md
```

模型先看目录，判断「这个任务像不像某个技能管的」，像才去加载正文。
**这是渐进式披露（progressive disclosure）** —— 和 s06 的子代理是同一个思路的两种形式：
子代理是对*执行过程*做隔离，技能加载是对*知识*做隔离。

## 代码怎么走

**第 1 步：技能就是一个带 frontmatter 的 markdown 文件。**

```markdown
---
name: code-review
description: Review code against a fixed narrow scope - syntax, function calls, types, empty implementations
---

# Code Review

Review code with a fixed, narrow scope. ...
```

规范约定：目录名 = 技能名，固定文件名 `SKILL.md`，frontmatter 里放元数据。
正文怎么写、写多长，全凭约定 —— **这些内容永远不会进入上下文，除非模型明确要它。**

**第 2 步：扫描器。**

```python
class SkillLoader:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills: dict[str, dict[str, str]] = {}
        self.scan()

    def scan(self):
        self.skills.clear()
        if not self.skills_dir.exists():
            return

        skills_root = self.skills_dir.resolve()
        for manifest in sorted(self.skills_dir.glob("*/SKILL.md")):
            if (not manifest.is_file()
                    or not manifest.resolve().is_relative_to(skills_root)):
                continue
            content = manifest.read_text(encoding="utf-8")
            metadata, body = self.parse_frontmatter(content)
            name = metadata.get("name") or manifest.parent.name
            ...
            self.skills[name] = {
                "name": name,
                "description": description,
                "content": content,        # ← 整个文件原样存着
            }
```

三处防御：

- **`sorted()`** —— 让目录顺序稳定。顺序不稳定的目录会让模型的判断在不同运行间漂移。
- **`is_relative_to(skills_root)`** —— 和 s02 `safe_path` 同样的越界防护。符号链接指向技能目录外面时会被挡掉。
- **`name` 缺失就退回目录名** —— frontmatter 是可选的，不写也能用，这样加技能的门槛就很低。

**第 3 步：frontmatter 解析。**

```python
@staticmethod
def parse_frontmatter(text: str) -> tuple[dict, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return {}, text
    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1)
         if line.rstrip("\r\n") == "---"),
        None,
    )
    if closing_index is None:
        return {}, text

    frontmatter = "".join(lines[1:closing_index])
    body = "".join(lines[closing_index + 1:]).strip()
    try:
        metadata = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return metadata, body
```

**`rstrip("\r\n")` 不是多余的。** 这个项目在 Windows 上开发，磁盘上的文件是 CRLF，
但 git 索引里存的是 LF —— 同一个文件在不同机器上读出来行尾不一样。
不统一剥掉的话，`"---\r" != "---"`，「文件里有 frontmatter」这件事会**只在 Linux 上成立**。

其他几处兜底也都指向同一个原则：**坏掉的技能不应该让整个 agent 起不来**。
没有 frontmatter → 用正文首行当描述；YAML 语法错 → 当作没有元数据；
frontmatter 解析出来不是 dict → 同上。三种情况都退化成「能扫到、描述可能不准」，
而不是抛异常。

**第 4 步：目录进系统提示。**

```python
def catalog(self) -> str:
    if not self.skills:
        return "(no skills found)"
    return "\n".join(
        f"- {skill['name']}: {skill['description']}"
        for skill in self.skills.values()
    )
```

```python
# utils/build_system_prompt.py
BASE = f"""...
When to use `load_skill`:
- The current task matches a skill listed below.
- You need the full instructions of that skill before acting.
- Do not load a skill speculatively; load it only when the task actually needs it.

Skills available:
{SKILL_LOADER.catalog()}
"""
```

渲染出来就是：

```
Skills available:
- code-review: Review code against a fixed narrow scope - syntax, function calls, types, empty implementations
```

最后那句 `Do not load a skill speculatively` 是在压制模型的「保险起见先都加载一遍」倾向 ——
那样的话渐近式披露就白做了，正文还是会全进上下文。

**第 5 步：按需加载。**

```python
@tool(description=(
        "Load the full SKILL.md content by skill name.\n\n"
        f'Available skills: {", ".join(SKILL_LOADER.skills) or "(none)"}'
))
def load_skill(name: str) -> str:
    skill = SKILL_LOADER.skills.get(name)
    if skill:
        return skill["content"]
    available = ", ".join(SKILL_LOADER.skills) or "none"
    return f"Error: Unknown skill '{name}'. Available: {available}"
```

返回的是**含 frontmatter 的整份原文** —— 正文里可能引用了 frontmatter 字段，剥掉反而丢信息。

报错时把可用列表一起返回，和 s02 `glob_files` 截断时那句 `(narrow the pattern)` 是同一个套路：
**错误信息里顺带告诉模型下一步该怎么做**，它就不用瞎猜第二遍。

> **这里踩了一个 Python 的坑，值得单独说。**
>
> 最初写的是：
>
> ```python
> @tool
> def load_skill(name: str) -> str:
>     f"""Load the full SKILL.md content by skill name.
>
>     Available skills: {", ".join(SKILL_LOADER.skills) or "(none)"}
>     """
> ```
>
> **`ast.parse` 通得过，Python 3.12 也不会报语法错误**（PEP 701 允许 f-string 里嵌套同类引号），
> 但程序一启动就炸：
>
> ```
> ValueError: Function must have a docstring if description not provided.
> ```
>
> 原因：**f-string 不是字符串字面量常量**，CPython 不把它当作 docstring，
> 于是 `load_skill.__doc__` 是 `None`，`@tool` 找不到描述就报错。
>
> 而且它炸得**极其靠前** —— `main_loop → tools_register → base_tools → skill_loader` 这条链一断，
> 整个 s07 一行都跑不了 —— 这是目前为止唯一一个「完全起不来」的章节。
>
> 改法是把动态描述交给 `description=`：
>
> ```python
> @tool(description=(
>     "Load the full SKILL.md content by skill name.\n\n"
>     f'Available skills: {", ".join(SKILL_LOADER.skills) or "(none)"}'
> ))
> def load_skill(name: str) -> str:
>     """Load the full SKILL.md content by skill name."""
> ```
>
> **教训**：`ast.parse` 通过 ≠ 代码能跑。

**第 6 步：主/子代理两套系统提示。**

```python
def build_system_prompt(role: str = "main") -> str:
    if role not in ("main", "sub"):
        raise ValueError(f"Unknown role: {role}")
    delegation = MAIN_DELEGATION if role == "main" else SUB_DELEGATION
    return f"{BASE}\n{delegation}"

SYSTEM_PROMPT = build_system_prompt(role="main")
SUB_SYSTEM = build_system_prompt(role="sub")
```

s06 里子代理的提示词是一小段字符串常量，主代理的是另一大段，两边**都要各自维护**。
这一章把它们拼装起来：**`BASE` 是共用的**（工具清单、技能目录），
后半段按角色换成「你负责统筹」或「你只负责执行这一段」。

技能目录对子代理也可见 —— 这是有意的：子代理同样可能遇到需要某项技能的任务。

> 另一个真实的坑：`MAIN_DELEGATION` 里把工具名写成了 `todo_write`，
> 而实际注册的名字是 `todo_writer`。模型照着提示去调，`execute_tool` 走到 else 分支返回
> `Unknown tool: todo_write` —— **不报错，只是白跑一轮**，而且 `main_loop` 里
> `tool_call["name"] == "todo_writer"` 的判断不成立，`rounds_since_todo` 一路涨到 3，
> 开始往对话里反复塞「更新你的 todo」。
>
> **系统提示是代码**：里面出现的名字和标识符，和 import 一样需要保持一致。

## 跑起来

```bash
uv run python s07_skill_loader/main_loop.py
```

先看看目录里有什么：

```bash
uv run python -c "
import sys; sys.path.insert(0, 's07_skill_loader')
from tools.skill_loader import SKILL_LOADER
print(SKILL_LOADER.catalog())
"
```

```
- code-review: Review code against a fixed narrow scope - syntax, function calls, types, empty implementations
```

然后在 agent 里问：

```
s07_skill_loader/utils/build_system_prompt.py 有什么问题吗
```

预期看到模型先调 `load_skill('code-review')`，再按加载到的范围给出结论：

```
[HOOK] load_skill(['code-review'])
[HOOK] Stop: session used 1 tool calls
```

**这就是渐进式披露跑通的样子**：系统提示里只有一行描述，
正文那几百字是模型判断「这个任务匹配」之后才拉进来的。

**加一个新技能**只要两步：

```bash
mkdir -p s07_skill_loader/skills/my-skill
$EDITOR s07_skill_loader/skills/my-skill/SKILL.md
```

```markdown
---
name: my-skill
description: 一句话说明它管什么 —— 这行是模型唯一能看到的
---

正文随便写多少，不进上下文。
```

不用改任何代码，下次启动时 `scan()` 会自动发现它。

## 目录结构的变化

```
s07_skill_loader/
├── config.py                   ★ 多了 SKILLS_DIR
├── skills/
│   └── code-review/SKILL.md    ★ 技能目录（示例技能）
├── tools/
│   ├── base_tools.py           ★ BASE_TOOLS 多了 load_skill
│   ├── skill_loader.py         ★ SkillLoader + load_skill
│   ├── subagent.py
│   ├── todo_writer.py
│   ├── file_tools.py
│   └── tools_register.py
├── utils/
│   ├── execute_tool.py
│   └── build_system_prompt.py  ★ 主/子两套提示词
├── hooks/
└── main_loop.py
```

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
| … | | 这张表会随章节继续往下长 |

**循环是骨架，机制是挂件。** 这大概是到目前为止最值得带走的一条。

---

## 下一步

s07 解决的是「知识放在哪儿」。但前面几章一路加下来，还有个一直没正面处理的问题：
**上下文只增不减。**

每一轮的工具输出都永久留在 `messages` 里 —— 一次 `read_file` 拉进 500 行，它就再也不会离开。
跑几十轮之后，历史里塞满早已无用的中间结果，既挤占窗口，也稀释模型的注意力。
s06 的子代理是对这个问题的局部缓解（把一趟探索隔离出去），但主对话本身仍然只涨不缩。

→ s08 Context Compact：怎么在保住关键信息的前提下，把历史压下去？
