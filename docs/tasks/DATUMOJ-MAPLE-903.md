---
id: DATUMOJ-MAPLE-903
title: 检查点 m3 集成：欧美与日本新来源、多语言、全部评测门槛一起达标（MAPLE 完成）；试运行与实机问答由人记录
project: DATUMOJ
status: doing
priority: 2
depends_on: ["DATUMOJ-MAPLE-902", "DATUMOJ-MAPLE-904", "DATUMOJ-MAPLE-144"]
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["tests/integration/test_maple_m3.py", "src/datumoj/wiring.py", "src/datumoj/rikolto/cli.py", "src/datumoj/mcp_server.py", "results/DATUMOJ-MAPLE-903/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "sources/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/index/**", "src/datumoj/rikolto/cloud.py", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "eval/questions/**", "src/datumoj/rikolto/eval/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/integration/test_maple_m3.py
    - cmd: make check T=tests/integration/test_maple_eval.py
    - cmd: make lint
    - cmd: make check T="tests --deselect=tests/integration/test_wisteria_m1.py::test_us_dollar_series_ranks_first_for_gdp_usd"
  evidence: ["测试输出日志", "变更文件清单", "全部题库的分数表", "results/DATUMOJ-MAPLE-903/summary.md 完成说明（含给人的 H4/H5 记录清单）"]
  review: {by: human, checklist: ["IVY、TACHIBANA、MAPLE 全部题库是否一起达标", "多语言题和库外题是否覆盖相关性门槛", "是否只改了接线、CLI、MCP 说明文字和集成测试", "是否越界"]}
  human: required
max_attempts: 3
---
# 目标
MAPLE 完成的自动化部分：目标 5–8 在同一个离线数据目录里一起成立——欧美来源（data.europa.eu、Eurostat、法国门户、Federal Register、WDI）和日本新来源（e-Stat API、国土数值信息、2024 经济普查、NDB、ABR）一起入库，日、中、英、欧洲语言问题都能检索到，库外问题判 `zero_hits`，IVY、TACHIBANA、MAPLE 的全部题一起达标。一周试运行（H4）和实机问答（H5）由人做，按 summary 的清单记进 `docs/acceptance/v0.3.0-maple.md`。

# 背景
开发线：集成。计划 `docs/plans/0.3-maple.md` 第 3 节目标 4–9、第 7 节 903 行（目标 11-16）。试运行期间没修好的来源在验收文档里写明，不等（“修好一个算一个”）。相关性门槛的修复在 MAPLE-144。

任务分支从 `feature/0.4` 拉出，合并回它。已有的 `tests/integration/test_maple_eval.py`（只做检索评测）和 `test_maple_m2.py`（日本五个来源）是起点，照它们的写法：真实解析器 + 录制样本，证据默认写临时目录，只有设置 `DATUMOJ_EVIDENCE_DIR` 时才写到该目录。
- 测试全部离线：HTTP 用 `tests/fixtures/` 的录制样本或本地假服务器；嵌入用假嵌入器。
- 依赖已锁定，不新增依赖，不改 `pyproject.toml`。
- 不改评测框架、题库和夹具；题目达不到门槛时不要改题，修接线；修不了就写进 summary。
- 不写账号、密钥、本机绝对路径。

# 要求
1. 新增 `tests/integration/test_maple_m3.py`：一个数据目录里同时放 IVY/TACHIBANA 已有来源的样本和 MAPLE 十个新来源的样本，经正式的抓取（回放）→ 解析 → 索引流程入库，然后通过 MCP 工具函数（`search_docs`、`search_catalog`、`query_sql`、`cite`、`list_changes`、`coverage`）回答：
   - 日、中、英各至少 2 题，德或法至少 1 题，第 1 位命中正确的来源，`cite` 能还原；
   - 跨国比较 1 题（例如日本与欧盟的同类统计），两边的出处都给出；
   - 库外问题日、英各 1 题判 `zero_hits`，不返回无关结果；
   - `coverage` 列出全部来源。
2. 用全部题库（IVY、TACHIBANA、MAPLE）跑评测，全部门槛通过；在证据里给出各题库的分数表。
3. summary 写给人的清单：
   - H4 一周试运行要记的东西（试运行起止时间、每次运行的状态、各来源最后成功时间、提醒和周报、JMA 有无缺口、R2 总量峰值），以及没修好的来源怎么写；
   - H5 实机问答的步骤（`rikolto pull` 到本机目录、在 Claude Code 里注册 MCP、日中英三种语言各问几题、核对出处和断言级别）和记录格式。
4. `make lint` 0 条，全量测试全部通过、要跑完；全量命令排除现存的 1 个 xfail，不改那个测试，不新增 xfail 或 skip。
5. 调度器里没有人能回答问题，不要等待回复。遇到范围缺口，在 scope 内选最小的做法完成，写进交付说明。

# 不做
- 不改采集器、解析器和索引（缺陷另开任务）。
- 不连真实 R2，不触发真实工作流，不访问真实网络和本机 `data/`。
- 不写验收文档（由人和主对话写）。
