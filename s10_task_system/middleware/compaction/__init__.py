# middleware/compaction/__init__.py
"""压缩中间件。目录图与逐条讲解见 ../README.md。

对外只有一个名字：Compaction(model, transcript_dir, tool_results_dir)。

**两个目录是必填的** —— 这个子包不认识 `config.py`，读本项目的配置是**层**的事
（middleware/__init__.py 的 build_compaction()）。

它也不持有被包裹的模型 —— 包裹由 middleware.inject() 完成，只实现
`around(messages, call_next)`，见 wiring.py。

内部按职责分五个模块：

    wiring.py            接线 —— 谁在什么时候调它
    context_compact.py   分区 + 配额 + 管线
    archive.py           两个存储 + 标记 + 指针
    tokenizer.py         官方词表的懒加载与 get_token
    estimate.py          消息 → token
"""
from middleware.compaction.wiring import Compaction

__all__ = ["Compaction"]
