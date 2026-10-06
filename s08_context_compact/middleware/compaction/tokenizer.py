# middleware/compaction/tokenizer.py
"""计量底层 —— 用 DeepSeek 官方词表做精确计数。

**词表不是代码。** 7.8MB 的 tokenizer.json 不进版本库：不在就自动拉一次，
落在 <仓库根>/.models/（已进 .gitignore），与 s08 的其余运行产物分开放。
"""
import hashlib
import urllib.request
from functools import lru_cache
from pathlib import Path

VOCAB_FILENAME = "deepseek-tokenizer.json"

# huggingface.co 在部分网络下不可达，hf-mirror.com 是它的镜像。
VOCAB_URL = "https://hf-mirror.com/deepseek-ai/DeepSeek-V3/resolve/main/tokenizer.json"

# **必须自己带 UA。** hf-mirror 对 urllib 的默认 UA 直接回 403，而 curl 能下 ——
# "顺手用 urlretrieve 拉一下"这条路径不真跑就发现不了。
VOCAB_UA = "Mozilla/5.0 (compatible; learn-agent tokenizer fetch)"

# 词表的**身份**。"与 API 差额恒为 30"那次验证是一次性的人肉结论，钉在这里
# 才不会被上游悄悄换掉；换一份就必须重做验证再改这个常量。
VOCAB_SHA256 = "621ac2e32d0dba658404412318818aaa8ce8cda492e59830109d8da6b517fb41"
VOCAB_MIN_BYTES = 1_000_000          # 远小于这个数说明下到的是错误页而不是词表


def _repo_root() -> Path:
    """往上找含 pyproject.toml 的那层，不写死 parents[N]（这文件搬过家）。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return here.parent


VOCAB_PATH = _repo_root() / ".models" / VOCAB_FILENAME

# 分词实测 2.78 MB/s，而早停循环里每处理一条就要重估整段 —— 缓存是必需不是优化。
_COUNT_CACHE_SIZE = 512

_tokenizer = None


def _get():
    """加载一次，全局复用；词表不在就自动拉一次。"""
    global _tokenizer
    if _tokenizer is None:
        if not VOCAB_PATH.exists():
            print(f"[tokenizer] 词表不在 {VOCAB_PATH}，拉一次（约 7.8MB）")
            ensure_vocab()
        from tokenizers import Tokenizer          # 懒导入：不做计数就不必装
        _tokenizer = Tokenizer.from_file(str(VOCAB_PATH))
    return _tokenizer


def verify_vocab(path: Path | None = None) -> str:
    """校验这个文件确实是那份词表，返回 sha256；不合格就**删掉它**并抛错。

    单独成一个函数是为了能**离线测** —— 把它指向一个小文件，就能验"它会拒绝垃圾"。
    """
    path = VOCAB_PATH if path is None else path
    size = path.stat().st_size
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if size < VOCAB_MIN_BYTES or digest != VOCAB_SHA256:
        path.unlink()
        raise ValueError(
            f"词表校验失败，已删除该文件：\n"
            f"  大小  实际 {size:,} B（下限 {VOCAB_MIN_BYTES:,}）\n"
            f"  指纹  期望 {VOCAB_SHA256}\n"
            f"        实际 {digest}\n"
            f"上游换过文件了。换一份就必须重做那次验证（判据是差额**恒定**，"
            f"不是随长度成比例），确认后才改 VOCAB_SHA256。"
        )
    return digest


def ensure_vocab(force: bool = False) -> Path:
    """确保词表就位：缺了就下载，然后**校验身份**。

    入口是 `_get()` —— 它在词表缺失时自动调这里，所以既没有脚本也没有 `-m`。
    """
    if VOCAB_PATH.exists() and not force:
        print(f"已存在，跳过：{VOCAB_PATH}（{VOCAB_PATH.stat().st_size:,} B）")
        return VOCAB_PATH

    VOCAB_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"下载 {VOCAB_URL}\n  -> {VOCAB_PATH}")
    request = urllib.request.Request(VOCAB_URL, headers={"User-Agent": VOCAB_UA})
    try:
        with urllib.request.urlopen(request, timeout=180) as resp:
            with VOCAB_PATH.open("wb") as f:
                while chunk := resp.read(1 << 20):
                    f.write(chunk)
    except Exception as e:
        raise RuntimeError(
            f"词表下载失败：{type(e).__name__}: {e}\n"
            f"  URL  {VOCAB_URL}\n"
            f"  目标 {VOCAB_PATH}\n"
            f"（① huggingface.co 不通是正常的，这里用的是 hf-mirror.com；"
            f"② 也可以手工把 tokenizer.json 放到目标路径，再调 verify_vocab() 自检）"
        ) from e

    digest = verify_vocab()
    print(f"完成并校验通过：{VOCAB_PATH.stat().st_size:,} B  sha256={digest[:16]}…")
    return VOCAB_PATH


@lru_cache(maxsize=_COUNT_CACHE_SIZE)
def _count(text: str) -> int:
    return len(_get().encode(text, add_special_tokens=False).ids)


def get_token(text: str) -> int:
    """text 的精确 token 数。"""
    return _count(text or "")


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """按 **token 数**截断（编码 → 切片 → 解码），供"预览"用。

    不用字符切片：字符→token 的换算随内容类型漂（实测跨度 1.52×），
    而那正是把整个计量换成真词表的理由。
    """
    if max_tokens <= 0:
        return ""
    ids = _get().encode(text or "", add_special_tokens=False).ids
    if len(ids) <= max_tokens:
        return text or ""
    return _get().decode(ids[:max_tokens])
