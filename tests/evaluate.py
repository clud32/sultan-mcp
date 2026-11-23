"""Evaluate retrieval against a fresh index from the configured installation."""
import csv
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory() as work:
        os.environ["SULTAN_STATE_DIR"] = work
        import kb
        from gamedata import Game
        index = kb.get_index(Game(), force=True)
        known = {doc["id"] for doc in index.docs}
        table = ROOT / "tests" / "queries.tsv"
        with table.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source, delimiter="\t"))
        groups = {}
        misses = []
        seen = set()
        for row in rows:
            query = row["query"]
            if query in seen:
                raise ValueError(f"重复问题 {query}")
            seen.add(query)
            targets = set(row["targets"].split(","))
            missing = targets - known
            if missing:
                raise ValueError(f"未找到参考文档 {sorted(missing)}")
            ids = [doc["id"] for _, doc in index.search(query, k=3)]
            hit1 = bool(ids and ids[0] in targets)
            hit3 = bool(targets.intersection(ids))
            group = groups.setdefault(row["group"], {"queries": 0, "top1": 0, "top3": 0})
            group["queries"] += 1
            group["top1"] += hit1
            group["top3"] += hit3
            if not hit3:
                misses.append(query)
        totals = {k: sum(g[k] for g in groups.values()) for k in ("queries", "top1", "top3")}
        print(json.dumps({**totals, "documents": len(known), "kb_version": kb.KB_VERSION,
                          "queries_sha256": hashlib.sha256(table.read_bytes()).hexdigest(),
                          "by_group": groups,
                          "top3_misses": misses}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
