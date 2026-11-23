"""Per-installation writable data for the local MCP server."""
import gzip
import json
import os
import re
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
STATE = Path(os.getenv("SULTAN_STATE_DIR") or PROJECT / ".local").expanduser().resolve()
BUNDLED_SEEDS = PROJECT / "kb_seeds"
PERSONAL_SEEDS = STATE / "kb_seeds"
CACHE = STATE / "kb_cache.json.gz"


def seed_files():
    for directory, prefix in ((BUNDLED_SEEDS, ""), (PERSONAL_SEEDS, "个人/")):
        for path in sorted(directory.glob("*.md")):
            if path.is_file():
                yield path, prefix + path.stem


def note_path(name):
    if not isinstance(name, str) or not re.fullmatch(r"[\w -]{1,64}", name):
        raise ValueError("笔记名只接受字母、汉字、数字、空格、下划线或短横线")
    path = PERSONAL_SEEDS / (name + ".md")
    if path.is_reserved() or path.resolve().parent != PERSONAL_SEEDS.resolve():
        raise ValueError("无效的笔记名")
    PERSONAL_SEEDS.mkdir(parents=True, exist_ok=True)
    return path


def read_docs(signature):
    try:
        with gzip.open(CACHE, "rt", encoding="utf-8") as stream:
            record = json.load(stream)
        if record["signature"] == json.dumps(signature):
            return record["docs"]
    except (OSError, EOFError, ValueError, KeyError, TypeError):
        pass
    return None


def write_docs(signature, docs):
    STATE.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=STATE, prefix="kb-", delete=False) as stream:
        tmp = Path(stream.name)
    try:
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=1) as stream:
            json.dump({"signature": json.dumps(signature), "docs": docs}, stream, ensure_ascii=False)
        os.replace(tmp, CACHE)
    finally:
        tmp.unlink(missing_ok=True)
