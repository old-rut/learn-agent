# s02: Tools — 把「一个工具」扩成「一套工具」

[s01](../s01_loop/) → `s02` → [s03](../s03_permission/) → [s04](../s04_hooks/) → [s05](../s05_todo_writer/) → [s06](../s06_subagent/) → [s07](../s07_skill_loader/) → s08 → s09 → s10 → s11 → s12 → s13 → s14 → s15 → s16 → s17

> *「工具不是越多越好，是越不容易被用错越好」*

---

## 问题

s01 里只有一个 `pwsh`。模型要读文件，只能自己拼一条命令：

```
Get-Content -Path foo.py -TotalCount 50
```

问题是这条命令是**模型现编的**。同一个意图，它这次写 `Get-Content`，下次可能写 `type`，再下次忘了加 `-TotalCount` 就把一个几百 KB 的文件整个灌进上下文。每次都要重新赌一遍它拼对没有。

写文件更糟 —— 用 `Set-Content` 写中文内容时编码要靠运气，覆盖已有文件时不会问一声。

## 方案

把最常做的事固化成工具。模型不再需要「想出一条命令」，只需要「填几个参数」：

| 工具 | 参数 | 干了什么 |
| --- | --- | --- |
| `read_file` | `path`, `limit?` | 读 UTF-8 文本，可选只读前 N 行 |
| `write_file` | `path`, `content` | 写文件（覆盖），自动建父目录 |
| `edit_file` | `path`, `old_text`, `new_text` | 替换**第一处**匹配 |
| `glob_files` | `pattern` | 按 glob 找文件，最多返回 200 条 |
| `pwsh` | `command` | 兜底的逃生舱，还在 |

参数名和类型由 `@tool` 从签名和类型标注里自动生成 JSON Schema，模型在调用前就能看到每个参数叫什么、是不是必填。

**主循环一个字都没改。**

## 代码怎么走

**第 1 步：先把「工作区」这个概念固定下来。**

```python
WORKDIR = Path(os.getcwd()).resolve()

def safe_path(path: str) -> Path:
    """把用户传入的路径解析到 WORKDIR 内，防止越界。"""
    p = (WORKDIR / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    if WORKDIR not in p.parents and p != WORKDIR:
        raise ValueError(f"Path escapes workspace: {path}")
    return p
```

三个文件工具全部先过 `safe_path`。关键在于先 `.resolve()` 再判断 —— 这样 `../../etc/passwd` 和 `C:\Windows\System32` 都会在解析之后被同一个条件挡住，而不是靠字符串匹配去猜。

注意 `WORKDIR` 是 **`os.getcwd()`**：你在哪儿启动程序，工作区就是哪儿。到 s04 会改成章节目录。

**第 2 步：每个工具都返回字符串，不抛异常。**

```python
@tool
def read_file(path: str, limit: int | None = None) -> str:
    """Read a UTF-8 text file. Optionally limit to the first N lines."""
    try:
        lines = safe_path(path).read_text(encoding="utf-8").splitlines()
    except Exception as e:
        return f"Error: {e}"
    if limit is not None:
        lines = lines[:limit]
    return "\n".join(lines)
```

`except` 里**返回** `f"Error: {e}"` 而不是 `raise` —— 这是整章的隐藏重点。工具的输出最终会变成一条 `ToolMessage` 回到模型手里；
如果把异常抛出去，循环就断了，你只会看到一个 traceback。返回错误字符串的话，模型读得到「文件不存在」，
它会自己换个路径再试一次。**让模型看见错误，它就能自我修正。**

**第 3 步：写文件顺手把父目录建出来。**

```python
@tool
def write_file(path: str, content: str) -> str:
    """Write content to a file (overwrites existing). UTF-8."""
    try:
        p = safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except Exception as e:
        return f"Error: {e}"
    return f"Wrote {len(content)} bytes to {path}"
```

写一个 `tmp/a/b/c.txt` 而 `tmp/a` 还不存在，是模型最常犯的错之一。与其让它学会先 `mkdir`，
不如在这里直接 `mkdir(parents=True, exist_ok=True)`。返回值里带上字节数，模型据此能确认自己真的写进去了。

**第 4 步：编辑只替换第一处。**

```python
if old_text not in text:
    return "Error: text not found"
p.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
```

`.replace(..., 1)` 的 `1` 是关键 —— 不加的话，文件中所有相同片段都会被替换掉。
先单独判断「找不到」并返回可读的错误，比直接 `replace` 然后返回一个假的成功要好。

**第 5 步：glob 要有上限。**

```python
matches = sorted(set(_glob.glob(pattern, root_dir=WORKDIR, recursive=True)))
shown = matches[:200]
if len(matches) > 200:
    shown.append("... (more matches omitted; narrow the pattern)")
```

`sorted(set(...))` 让结果稳定（同一个 pattern 每次返回同样顺序，模型的行为才可复现）。
截断到 200 条并**明确告诉模型被截断了** —— 悄悄截断的话，模型会以为「就这么几个文件」，
然后基于错误的事实往下推理。最后那句话还顺带教了它下一步该怎么做（narrow the pattern）。

## 跑起来

```bash
uv run python s02_tools/agent_add_tools.py
```

试试这些：

1. `读一下 s01_loop/agent_mvp.py 的前 30 行`（看它选 `read_file` 还是 `pwsh`）
2. `在 tmp/ 下建一个 a/b/note.txt，内容是 hello`（父目录不存在，看 `write_file` 兜不兜得住）
3. `把 note.txt 里的 hello 改成 hello world`
4. `找出所有 .py 文件里用到 todo 的地方`（看它怎么组合 `glob_files` 和 `read_file`）
5. `读一下 C:\Windows\System32\drivers\etc\hosts`（应该被 `safe_path` 挡住）

重点看**第 5 条**：工作区防护挡住越界访问时，模型收到的是一条 `Error: Path escapes workspace: ...`，
它接下来会怎么反应？大概率会老实换回工作区内的路径 —— 这就是「返回错误字符串」的价值。

> ⚠️ 这一章的 `pwsh` **仍然是 s01 那份 Linux 黑名单**，没有任何权限检查。
> 工具变多了，但 `pwsh` 这个口子还是完全敞开的。

## 下一步

五个工具让模型终于能干活了，但 `pwsh` 是一扇不上锁的门 —— 模型可以删文件、改注册表、关防火墙，你连知情权都没有。

→ [s03](../s03_permission/)：怎么在「不打断正常任务」和「不放过危险命令」之间划那条线？
