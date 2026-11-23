"""Run with python -m unittest discover -s tests -v; no game installation required."""
import json
import tempfile
import unittest
from pathlib import Path

from mcp_client import PROTOCOL, content, run_session


class MCPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        root = Path(tmp.name)
        saves = root / "saves"
        saves.mkdir()
        cls.state = root / "state"
        game = root / "game"
        game.mkdir()
        config = game / "Sultan's Game_Data" / "StreamingAssets" / "config"
        config.mkdir(parents=True)
        (config / "cards.json").write_text("{}", encoding="utf-8")
        data = {"round": 1, "cards": [], "rites": []}
        (saves / "auto_save.json").write_text(json.dumps(data), encoding="utf-8")
        (saves / "global.json").write_text('{"mods":[]}', encoding="utf-8")
        env = {"SULTAN_STATE_DIR": str(cls.state), "SULTAN_SAVE_DIR": str(saves),
               "SULTAN_GAME_DIR": str(game), "SULTAN_WORKSHOP_DIR": str(root / "mods")}
        calls = [
            ("saves", {}), ("overview", {}),
            ("unknown_tool", {}),
            ("kb_add_seed", {"file": "测试", "title": "测试主题", "text": "本地笔记"}),
            ("kb_add_seed", {"file": "子目录/笔记", "title": "测试", "text": "测试"}),
        ]
        cls.replies = run_session(root, env, calls, version="unsupported-test-version")
        cls.saves = saves

    def test_initialize_and_ping(self):
        self.assertEqual(self.replies[0]["protocolVersion"], PROTOCOL)
        self.assertIn("tools", self.replies[0]["capabilities"])
        self.assertEqual(self.replies[1], {})

    def test_tool_discovery(self):
        tools = self.replies[2]["tools"]
        names = {tool["name"] for tool in tools}
        self.assertEqual(len(tools), len(names))
        self.assertEqual(len(names), 22)
        self.assertTrue({"saves", "kb_search", "kb_get", "daily_report"} <= names)
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_reads_fixture_save(self):
        self.assertEqual(content(self.replies[3])["files"], ["auto_save.json"])
        self.assertEqual(content(self.replies[4])["round"], 1)

    def test_round_snapshot_without_active_autosave(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            profile = folder / "profile"
            profile.mkdir()
            (profile / "round_8_end.json").write_text('{"round":8}', encoding="utf-8")
            (profile / "round_invalid.json").write_text("{}", encoding="utf-8")
            env = {"SULTAN_SAVE_DIR": str(folder), "SULTAN_STATE_DIR": str(folder / "state"),
                   "SULTAN_GAME_DIR": str(folder), "SULTAN_WORKSHOP_DIR": str(folder)}
            results = run_session(folder, env, [("overview", {})])
            value = content(results[3])
            self.assertEqual((value["round"], value["save"]), (8, "round_8_end.json"))

    def test_unknown_tool(self):
        self.assertTrue(self.replies[5]["isError"])

    def test_notes_are_private_to_the_state_directory(self):
        note = Path(content(self.replies[6])["appended_to"])
        self.assertEqual(note, self.state / "kb_seeds" / "测试.md")
        self.assertIn("本地笔记", note.read_text(encoding="utf-8"))
        self.assertTrue(self.replies[7]["isError"])
        self.assertFalse((self.state / "kb_seeds" / "子目录").exists())


if __name__ == "__main__":
    unittest.main()
