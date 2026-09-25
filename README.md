# learn-agent

用 **LangChain + DeepSeek** 从零实现一个类 Claude Code 的编码智能体。

每章引入一个新机制，主循环的核心结构自始至终不变 —— 变的只是循环周围的东西。

> 课程结构参考 [shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code)，
> 本项目是它的重新实现 —— 代码完全重写为 LangChain 的 `init_chat_model` / `@tool` / `bind_tools`，
> 运行环境改为 Windows + PowerShell 7。详见文末[致谢](#致谢)。

---

## 章节

与原项目一一对应，共 17 章。**带链接的是已实现的章节**，其余在陆续补上：

| 章节 | 主题 | 关键技术点 |
| --- | --- | --- |
| [s01 Agent Loop](s01_loop/) | 一个 `while` + 一个工具 | `messages` / `tool_calls` / 结果回流 |
| [s02 Tools](s02_tools/) | 5 个工具 + 工作区防护 | 工具 schema / `safe_path` / 错误返回而非抛出 |
| [s03 Permission](s03_permission/) | 三层权限门控 | 硬拒绝 / 规则匹配 / 用户审批 / PowerShell 别名 |
| [s04 Hooks](s04_hooks/) | 扩展逻辑与主循环解耦 | `PreToolUse` / `PostToolUse` / `Stop` |
| [s05 TodoWriter](s05_todo_writer/) | 先计划再执行 | Pydantic schema / 跨元素校验 / 三轮催促 |
| [s06 Subagent](s06_subagent/) | 上下文隔离 | 全新 `messages` / 只回传最终结论 |
| [s07 SkillLoader](s07_skill_loader/) | 技能按需加载 | 目录常驻 / frontmatter 扫描 / 渐进式披露 |
| s08 Context Compact | 上下文压缩 | 工具结果预算 / 微压缩 / 历史摘要 |
| s09 Memory | 记忆系统 | 选取 / 抽取 / 归并 |
| s10 Task System | 任务系统 | `TaskRecord` / `blockedBy` / 落盘持久化 |
| s11 Background Tasks | 后台任务 | 线程执行 / 通知队列 |
| s12 Cron Scheduler | 定时调度 | 持久化调度 / 会话级触发 |
| s13 Agent Teams | 智能体团队 | 常驻队友 / 原子认领 / 任务级 worktree / 类型化协议 |
| s14 MCP Plugin | MCP 插件 | 工具发现 / 命名空间 / 工具池装配 |
| s15 Integrated Harness | 集成 | 工具、上下文、任务、团队、调度、MCP 围绕一个循环 |
| s16 Workflow Runtime | 工作流运行时 | 脚本编排 / 生命周期事件 / journal 恢复 |
| s17 Goal Loop | 目标循环 | 目标闸门 / 会话评估 / 自动续跑 |

每章目录下的 README 都以「问题 → 方案 → 代码怎么走 → 跑起来」的顺序讲这一章，
顶部导航行标出它在整条路线上的位置。

## 环境

- Python ≥ 3.12
- Windows + PowerShell 7（路径写在 `config.py` 的 `PS7_PATH`，默认 `C:\Program Files\PowerShell\7\pwsh.exe`）
- [uv](https://docs.astral.sh/uv/) 管理依赖
- 一个 DeepSeek API Key

```bash
uv sync
cp .env.example .env      # 然后填入 DEEPSEEK_API_KEY
```

`.env` 会被 `.gitignore` 排除，不会被提交。

## 运行

各章入口脚本所在目录会进入 `sys.path[0]`，所以**从仓库根直接跑**即可：

```bash
uv run python s01_loop/agent_mvp.py
uv run python s04_hooks/main_loop.py
uv run python s07_skill_loader/main_loop.py
```

进入后输入问题、回车发送，输入 `q` / `exit` 退出。

## 架构演进

目录结构随章节推进逐步演化。开头几章是一个文件，之后慢慢拆成包：

```
s01_loop/           agent_mvp.py                     单文件
s02_tools/          agent_add_tools.py               单文件
s03_permission/     agent_permission.py              单文件
s04_hooks/          main_loop.py + config.py
                    tools.py                         工具集中在一个文件
                    hooks/                           4 种事件 + 5 个钩子
s05_todo_writer/    main_loop.py + config.py
                    tools/                           工具拆成包
                        file_tools.py pwsh.py todo_writer.py tools_register.py
                    hooks/
s06_subagent/       main_loop.py
                    tools/base_tools.py              叶子模块，用来打断循环导入
                    tools/subagent.py
                    utils/execute_tool.py
s07_skill_loader/   main_loop.py
                    utils/build_system_prompt.py     主/子代理两套系统提示
                    tools/skill_loader.py            SKILL.md 扫描 + 按需加载
                    skills/code-review/SKILL.md      示例技能
```

## 两个容易踩的点

**1. s01–s03 和 s04+ 的 `WORKDIR` 不一样**

- s01–s03：`WORKDIR = Path(os.getcwd()).resolve()` —— 你**在哪儿启动**，工作区就是哪里
- s04–s07：`WORKDIR = Path(__file__).resolve().parent` —— 永远是**章节目录本身**

`safe_path()` 的越界防护、`glob_files` 的搜索根都以 `WORKDIR` 为准，所以同一句「读一下 xxx.py」在不同章里可能落到不同目录。

**2. 这些导入是「章节目录级」的绝对导入**

各章内部写的是 `from tools.pwsh import pwsh` 这种绝对导入，靠的是 `sys.path[0]` = 入口脚本所在目录。所以：

```bash
uv run python s04_hooks/main_loop.py     # ✅
uv run python -m s04_hooks.main_loop     # ❌ ModuleNotFoundError: No module named 'tools'
```

`-m` 会把 `sys.path[0]` 设成仓库根，根下没有 `tools` 这个包。

## 致谢

章节划分与教学顺序参考 [shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code)，
该项目以 **MIT 许可证**发布，版权归其作者所有。

这不是 fork，是一次**重新实现**。沿用下来的有「每章引入一个机制、主循环本身不变」这条推进方式、
章节划分，以及各章节 README 的组织形式 —— s01 的引言即译自该项目的章节 README。

代码全部独立编写，模型接入用的是 LangChain 的
`init_chat_model` / `@tool` / `bind_tools`，运行环境是 Windows + PowerShell 7。

如果把这里当学习材料，建议两个仓库对照着读 —— 同一个机制，两套实现思路。

## 说明

- 代码只在 **Windows + PowerShell 7** 上验证过，`PS7_PATH` 是硬编码的，换平台需要改 `config.py`。
- 项目里没写测试。各章的验证方式是直接跑起来对话。
