"""Evaluate retrieval against a fresh index from the configured installation."""
import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workshop-ids", nargs="*", metavar="ID",
                        help="按给定顺序加载已安装模组。仅传选项时使用基础配置，省略选项时读取玩家存档。")
    mods = parser.parse_args().workshop_ids
    with tempfile.TemporaryDirectory() as work:
        os.environ["SULTAN_STATE_DIR"] = work
        if mods is not None:
            profile = Path(work) / "profile"
            profile.mkdir()
            (profile / "auto_save.json").write_text("{}", encoding="utf-8")
            (profile / "global.json").write_text(json.dumps({"mods": mods}), encoding="utf-8")
            os.environ["SULTAN_SAVE_DIR"] = str(profile)
        import kb
        from gamedata import Game
        game = Game()
        loaded_mods = [mod_id for mod_id, _ in game.mod_dirs()]
        if mods is not None and loaded_mods != mods:
            parser.error(f"请检查模组安装和 SULTAN_WORKSHOP_DIR。指定 {mods}，实际加载 {loaded_mods}")
        index = kb.get_index(game, force=True)
        known = {doc["id"] for doc in index.docs}
        corpus_hash = hashlib.sha256()
        for doc in index.docs:
            fields = [doc[key] for key in ("id", "kind", "name", "text")]
            record = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
            corpus_hash.update((record + "\n").encode("utf-8"))
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
                          "workshop_ids": loaded_mods,
                          "corpus_sha256": corpus_hash.hexdigest(),
                          "queries_sha256": hashlib.sha256(table.read_bytes()).hexdigest(),
                          "by_group": groups,
                          "top3_misses": misses}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
