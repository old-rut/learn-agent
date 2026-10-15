# -- Recall --
import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from utils.build_system_prompt import SYSTEM_PROMPT
from utils.extract_text import extract_text
from hooks.memory_hook.memory_store import RECALL_CHAR_LIMIT
from hooks.memory_hook.memory_store import list_memory_files, read_memory_file, read_memory_index, extract_json_array
from hooks.memory_hook.memory_store import model

# Recall

# 目录是 markdown 链接、记录带 source 文件名，模型会把它们读成"可打开的文件"
# 而跑去 read_file —— 全文其实已经注入。这句话就是来堵这个的。
RECALL_NOTE = (
    "Below is long-term memory about the user, recalled from earlier sessions. "
    "The content is already included here — treat it as known background and "
    "do not read the memory files again unless it is genuinely necessary."
)


def recent_user_text(messages: list, max_turns: int = 3) -> str:
    turns = []
    for message in reversed(messages):
        if not isinstance(message, HumanMessage):
            continue
        text = extract_text(message.content).strip()
        if text:
            turns.append(text)
        if len(turns) == max_turns:
            break
    return "\n".join(reversed(turns))[:4000]

def keyword_memory_selection(
    records: list[dict], query: str, max_items: int
) -> list[str]:
    words = set(
        re.findall(r"[a-z0-9_]{3,}|[\u4e00-\u9fff]{2,}", query.lower())
    )
    ranked = []
    for record in records:
        catalog_text = f"{record['name']} {record['description']}".lower()
        score = sum(word in catalog_text for word in words)
        if score:
            ranked.append((score, record["filename"]))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [filename for _, filename in ranked[:max_items]]

def select_relevant_memories(messages: list, max_items: int = 5) -> list[str]:
    records = list_memory_files()
    query = recent_user_text(messages)
    if not records or not query:
        return []

    catalog = "\n".join(
        f"{index}: {' '.join(record['name'].split())} - "
        f"{' '.join(record['description'].split())}"
        for index, record in enumerate(records)
    )
    prompt = (
        "Select memory records that are relevant to the current user request. "
        "Return only a JSON array of catalog indices, such as [0, 2]. "
        "Return [] when none are relevant.\n\n"
        f"Current request:\n{query}\n\nMemory catalog:\n{catalog[:12000]}"
    )
    call = [SystemMessage(content=prompt)]
    try:
        response = model.invoke(call, max_tokens=200)
        indices = extract_json_array(
            extract_text(response.content)
        )
        selected = []
        for index in indices:
            if isinstance(index, int) and 0 <= index < len(records):
                filename = records[index]["filename"]
                if filename not in selected:
                    selected.append(filename)
                if len(selected) == max_items:
                    break
        return selected
    except Exception:
        return keyword_memory_selection(records, query, max_items)

def load_memories(messages: list) -> None:
    print("\033[90m[HOOK] PreAgent: recalling memories\033[0m")
    loaded = []
    remaining = RECALL_CHAR_LIMIT
    for filename in select_relevant_memories(messages):
        content = read_memory_file(filename)
        if not content or remaining <= 0:
            continue
        recalled = content[:remaining]
        loaded.append({"source": filename, "content": recalled})
        remaining -= len(recalled)
    relevant_memories = json.dumps(loaded, ensure_ascii=False, indent=2) if loaded else ""

    index = read_memory_index()

    content = SYSTEM_PROMPT
    if index or relevant_memories:          # 没有东西可注入时别硬加说明和空标题
        content += f"\n{RECALL_NOTE}"
    if index:
        content += f"\nMemory catalog:\n{index}"
    if relevant_memories:
        content += f"\nRelevant memory records:\n{relevant_memories}"
    for message in messages:
        if isinstance(message, SystemMessage):
            message.content = content
            break
