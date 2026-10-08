# -- Manual memory commands --
"""手动记忆：`/remember` 直接落盘，`/memories` 看现存什么。

`/remember` **不经过 should_store_memory**。那套启发式（scope 门槛、临时
字眼、去重）是用来替用户**猜**"这条值不值得长期记住"的；用户一旦明说
"记住"，就没有可猜的了 —— 拿猜测器去否决一条指令是用错了权威。

模型在这里只干一件事：把一段自由文本**拆**成 name/description/body。
去留由用户决定。
"""
from langchain_core.messages import SystemMessage

from hooks.memory_hook.extract_memory import validate_memory_record
from hooks.memory_hook.memory_store import (
    extract_json_array, list_memory_files, model, write_memory_file)
from utils.extract_text import extract_text

PARSE_PROMPT = (
    "Turn the user's instruction into one memory record. "
    "Return a JSON array with exactly one object having name, description, "
    "and body. `name` is a short title, `description` is one line, `body` "
    "keeps the full detail verbatim. Do not judge whether it is worth "
    "remembering — the user already asked for it. Return [] only if the "
    "instruction is empty."
)


def _parse(text: str) -> dict | None:
    """一次模型调用：把一句话拆成一条记录。拆不出来就返回 None（并说明看到了什么）。"""
    prompt = f"{PARSE_PROMPT}\n\nInstruction:\n{text}"
    try:
        response = model.invoke([SystemMessage(content=prompt)], max_tokens=500)
    except Exception as error:
        print(f"\033[33m[memory] 整理失败：{error}\033[0m")
        return None

    raw = extract_text(response.content)
    for item in extract_json_array(raw):
        if not isinstance(item, dict):
            continue
        # type 由这一层定死，不问模型 —— 用户亲手 /remember 的就是 user。
        # 让模型复述一个已经定死的字段，只会多一个出错的地方。
        record = validate_memory_record({**item, "type": "user"})
        if record:
            return record
    # 失败要说清楚看到了什么，否则只能靠猜
    print(f"\033[33m[memory] 模型没给出可用的记录，原文：{raw.strip()[:200]!r}\033[0m")
    return None


def remember(args: str, messages) -> None:
    """`/remember <内容>` —— 用户明说要记，那就记。"""
    if not args:
        print("\033[33m用法：/remember <要记住的内容>\033[0m")
        return
    record = _parse(args)
    if record is None:
        return                      # _parse 已经说明原因了
    path = write_memory_file(
        record["name"], "user", record["description"], record["body"])
    print(f"\033[33m[memory] 已记住 [{record['name']}] -> {path.name}\033[0m")
    print(f"         {record['description']}")


def memories(args: str, messages) -> None:
    """`/memories` —— 让用户能确认自己那句话到底进去没有。"""
    records = list_memory_files()
    if not records:
        print("\033[33m[memory] 还没有任何记忆\033[0m")
        return
    print(f"\033[33m[memory] {len(records)} 条：\033[0m")
    for record in records:
        print(f"  [{record['type']}] {record['name']} — {record['description']}")
