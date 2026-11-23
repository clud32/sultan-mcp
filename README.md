# 苏丹的游戏 MCP

在 MCP 客户端里问「这个仪式还差哪张卡，谁有资格进这个格子？」服务会从玩家的回合存档读取卡牌和人物，再按基础配置及创意工坊模组的加载顺序核对条件。也可以用它查剧情分支，或生成当天的行动清单。

## 本地运行

准备 64 位 Python 3.11 和 Windows 版《苏丹的游戏》。服务只用 Python 标准库，在 CPU 上完成索引和检索。先在仓库根目录运行以下命令，确认工具能列出自己的存档。

```powershell
$env:SULTAN_GAME_DIR = "D:/SteamLibrary/steamapps/common/Sultan's Game"
python -X utf8 server.py saves
```

在支持 stdio 的 MCP 客户端中添加以下配置，把 `D:/repos/sultan-mcp` 和游戏目录换成本机路径。如果桌面客户端找不到 `python`，可在 PowerShell 运行 `(Get-Command python).Source`，把结果填入 `command`。保存配置后重新加载服务。

```json
{
  "mcpServers": {
    "sultan": {
      "command": "python",
      "args": [
        "-X",
        "utf8",
        "D:/repos/sultan-mcp/server.py"
      ],
      "env": {
        "SULTAN_GAME_DIR": "D:/SteamLibrary/steamapps/common/Sultan's Game"
      }
    }
  }
}
```

存档默认从 Windows 用户目录的 `AppData/LocalLow/DoubleCross/SultansGame/SAVEDATA` 查找。用 `SULTAN_SAVE_DIR` 可指定单个档案目录。默认 `auto` 优先读 `auto_save.json`，结局后仅剩回合快照时读取最近修改的 `round_N.json` 或 `round_N_end.json`。`overview.save` 显示实际读取的文件名。模组目录默认取同一 Steam 库下的 `steamapps/workshop/content/3117820`，跨库安装时可设置 `SULTAN_WORKSHOP_DIR`。

## 能查什么

| 用途 | MCP 工具 |
| --- | --- |
| 局面与存档 | `overview`、`cards`、`rites`、`counters`、`saves`、`diff`、`log`、`upcoming` |
| 格子、条件和路线 | `config`、`search`、`slots`、`cross_check`、`long_term`、`line_status` |
| 行动记录 | `daily_report`、`save_advice`、`journal`、`goals` |
| 攻略知识库 | `kb_search`、`kb_get`、`kb_add_seed`、`kb_rebuild` |

例如，先让客户端用 `overview` 查看当天仍在进行的仪式，再把目标仪式的 `uid` 交给 `cross_check`。它会列出每格的要求，并区分当前空闲的人选与正在别处忙碌的人选。要追查某个判断，可以用 `config` 核对生效配置和来源。 附有可直接交给客户端的[军师提示词](军师提示词.md)。

每项仪式、事件、卡牌和结局都有独立文档 ID。配置解析保留重复键，便于追查同名条件的多个分支。 检索使用 BM25，中文按单字和相邻双字切分，对名称和攻略笔记加权。`kb_search` 返回片段与 ID，`kb_get` 按 ID 返回整篇。游戏或模组更新后可用 `kb_rebuild` 从本机文件重新索引。

## 本地数据与许可

游戏安装目录和存档是只读输入。日报、目标、个人笔记和索引缓存写入仓库根目录下的 `.local/`，也可用 `SULTAN_STATE_DIR` 指定其他目录。读取局面时可能自动生成当日日报。程序自带的攻略笔记在 `kb_seeds/`，`kb_add_seed` 默认写入 `.local/kb_seeds/`。

本项目的代码、测试和自编文档使用 [MIT 许可证](LICENSE)。游戏本体、创意工坊模组由玩家自行安装。个人存档、历史报告和从游戏生成的完整索引均仅保存在玩家本机。

## 测试环境声明

2026-11-24 在 Windows 11 家庭中文版（10.0.26200，AMD64）、CPython 3.11.9 64 位、Steam Windows 版 Build ID `25901963` 上运行。MCP 客户端通过子进程的标准输入输出协商协议 `2025-06-18`。安装启用了 3 个带配置的创意工坊模组，真实读档测试使用结局后保留的回合快照。 每次集成测试单独创建临时状态目录，从空缓存开始。详细环境与原始计数见[测试记录](tests/results.json)。

## 测试结论

| 检查项目 | 本次结果 |
| --- | --- |
| 无游戏安装的回归测试 | 6/6 通过，覆盖握手、工具列表、读档、未知工具和笔记写入范围，另验证缺少自动存档时读取回合快照 |
| 真实安装的 stdio 集成测试 | 发现 22 个工具，调用其中 7 个。读取卡牌、核对生效的卡牌配置并生成日报，回合存档的 SHA-256 在测试前后一致 |
| BM25 首条命中 | 106/170（62.4%） |
| BM25 前三条命中 | 136/170（80.0%），从本机游戏和 32 条随附笔记构建 4,850 篇文档 |

[评测题集](tests/queries.tsv)中的每个问题标有一个或多个可接受的文档 ID。前 K 条结果出现任一参考 ID 即计一次命中。题集包含同一机制的多种问法，衡量的是这些问题的资料召回能力。34 道题未在前三条命中，包括用别称描述的分支与跨路线问题。 回答质量、通关成功率、其他操作系统和桌面客户端界面均未纳入本轮测试。

在仓库根目录可复跑以下命令。第一条使用合成存档，第二条需要游戏安装和存档，第三条按当前安装重建知识库，输出命中统计和未命中问题。

```powershell
python -X utf8 -m unittest discover -s tests -v
python -X utf8 tests/live_smoke.py
python -X utf8 tests/evaluate.py
```
