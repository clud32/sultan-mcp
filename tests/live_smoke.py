"""Exercise the installed game and active save through the real stdio server."""
import hashlib
import json
import tempfile
from pathlib import Path

from mcp_client import PROTOCOL, content, run_session


def main():
    calls = [("saves", {}), ("overview", {}), ("kb_search", {"query": "妻子的不满", "k": 3}),
             ("kb_get", {"id": "seed:经验#1"}), ("daily_report", {}),
             ("cards", {"limit": 2}), ("config", {"kind": "card", "id": "2000861", "path": "name"})]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        state = work / "state"
        from_paths = save_inputs()
        before = {path: hashlib.sha256(path.read_bytes()).digest() for path in from_paths}
        hello, ping, tools, *results = run_session(work, {"SULTAN_STATE_DIR": str(state)}, calls)
        assert hello["protocolVersion"] == PROTOCOL and ping == {}
        names = {item["name"] for item in tools["tools"]}
        assert all(name in names for name, _ in calls)
        saves, overview, search, doc = map(content, results[:4])
        assert saves["files"] and isinstance(overview.get("round"), int)
        assert search["hits"] and doc["id"] == "seed:经验#1" and doc["text"]
        cards, config = map(content, results[5:])
        assert isinstance(cards["cards"], list) and len(cards["cards"]) <= 2
        assert config["id"] == "2000861" and config["sources"] and config["data"]
        report = results[4]
        if report.get("isError"):
            raise RuntimeError(report["content"][0]["text"])
        files = list((state / "reports").rglob("day_*.md"))
        # Reports may exceed the MCP display limit. The file path comes before the excerpt.
        reply = report["content"][0]["text"]
        assert any(json.dumps(str(p), ensure_ascii=False) in reply
                   and p.read_text(encoding="utf-8").startswith("# ") for p in files)
        assert (state / "kb_cache.json.gz").is_file()
        assert before and all(hashlib.sha256(p.read_bytes()).digest() == d for p, d in before.items())
        print(json.dumps({"tools": len(names), "calls": [n for n, _ in calls],
                          "reports_created": len(files), "save_files_verified": len(before)},
                         ensure_ascii=False))


def save_inputs():
    import sys
    from mcp_client import ROOT
    sys.path.insert(0, str(ROOT))
    from gamedata import Game
    return [Game().save_path("auto")]


if __name__ == "__main__":
    main()
