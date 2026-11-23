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
        top1 = top3 = 0
        misses = []
        seen = set()
        for row in rows:
            query = row["query"]
            if query in seen:
                raise ValueError(f"重复问题 {query}")
            seen.add(query)
            targets = set(row["targets"].split(","))
            if not targets & known:
                raise ValueError(f"未找到参考文档 {targets}")
            ids = [doc["id"] for _, doc in index.search(query, k=3)]
            top1 += bool(ids and ids[0] in targets)
            if targets.intersection(ids):
                top3 += 1
            else:
                misses.append(query)
        print(json.dumps({"queries": len(rows), "documents": len(known), "top1": top1,
                          "top3": top3, "kb_version": kb.KB_VERSION,
                          "queries_sha256": hashlib.sha256(table.read_bytes()).hexdigest(),
                          "top3_misses": misses}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
