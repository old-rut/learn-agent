

def extract_text(content) -> str:
    if not isinstance(content, list):
        return str(content)
    parts = []
    for block in content:
        # LangChain 的块可能是 dict，也可能是带属性的对象
        btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if btype == "text":
            text = block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
            parts.append(text)
    return "\n".join(parts)