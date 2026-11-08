---
id: DATUMOJ-MAPLE-902
title: 检查点 m2：日本新来源（e-Stat API、KSJ、经济普查、NDB、ABR）离线端到端；真实抓取由人记录
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-900", "DATUMOJ-MAPLE-301", "DATUMOJ-MAPLE-304", "DATUMOJ-MAPLE-305"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["tests/integration/test_maple_m2.py", "src/datumoj/rikolto/wiring.py", "src/datumoj/rikolto/cli.py", "results/DATUMOJ-MAPLE-902/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env*", "sources/**", "catalog/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "tests/regressions/**", "src/datumoj/rikolto/core/models.py", "src/datumoj/rikolto/core/contracts.py", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/integration/test_maple_m2.py
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-902/summary.md 完成说明（含真实抓取清单）"]
  review: {by: human, checklist: ["五个日本新来源是否在同一个数据目录里一起跑通，而不是各自单测拼起来", "数值、单位、遮蔽注记、前导零是否与样本原文一致", "出处是否都能 cite 还原到快照，e-Stat 的 URL 是否都是 appId=REDACTED", "真实抓取清单是否完整且不含任何密钥值", "是否只改了接线、CLI 和集成测试", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
MAPLE 检查点 m2：0.3 新加的五个日本来源（`jp-estat-api`、`jp-ksj`、`jp-census`、`jp-mhlw`、`jp-abr`）用真实实现在**同一个数据目录**里一起跑通抓取、解析、索引、检索、引用和 `query_sql`，并有证据；真实抓取由人按清单做一遍，记到 `docs/acceptance/v0.3.0-maple-m2.md`。

# 背景
开发线：集成。计划 `docs/plans/0.3-maple.md` 第 3 节目标、第 5 节任务表（902 一行）；来源决策 `docs/plans/0.3-maple-sources.md` 第 3、4、6 节。依赖的 301、304、305 和 m0（900）都已合并进 `feature/0.4`。

本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。本任务合并、真实抓取记录完、人工验收通过后，向 `main` 提 PR 同步，tag `v0.3.0-maple-m2` 等验收后由主对话打。
- **只依赖契约和已合并模块**：数据模型在 `src/datumoj/rikolto/core/models.py`，接口协议在 `src/datumoj/rikolto/core/contracts.py`，假实现在 `src/datumoj/rikolto/testing/`。集成任务可以 import 各来源的采集器和解析器，但不改它们；发现来源实现的缺陷时写进 `summary.md` 并停下来问。
- 只允许改接线（`wiring.py`）、CLI 和新的集成测试；契约不改。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 的样本（`ReplayTransport`）或 `pytest-httpserver`。样本只读。
- e-Stat 的 appId：测试里用 `monkeypatch.setenv('ESTAT_APP_ID', ...)` 设一个假值，绝不读取真实环境里的值；本任务的任何输出（日志、summary、证据文件）都不能出现 appId。
- 依赖已锁定，不能新增依赖、不能改 `pyproject.toml` / `uv.lock`；仓库是公开的，不写账号、密钥、本机绝对路径；临时文件放 `tmp_path`。

# 要求
1. 集成测试 `tests/integration/test_maple_m2.py`：建一个数据目录，五个来源都从各自样本离线抓取（`tests/fixtures/estat_api/`、`ksj/`、`census2024/`、`ndb/`、`abr/`，用法看各目录 README 和已有的 `test_estat_api_e2e.py`、`test_jp_files_e2e.py`、`test_jp_files2_e2e.py`），然后解析、建索引，再通过 MCP 工具（`search_docs`、`search_catalog`、`cite`、`query_sql`）断言：
   - **e-Stat API**：`query_sql` 取到 2021 年 12 月総人口 12547（万人），出处 URL 为 `appId=REDACTED`；多页响应（样本第 1 页带 `NEXT_KEY`）不写入任何观测值；`search_catalog` 能按表题找到人口推計的统计表。
   - **经济普查、KSJ**：各取一个样本 README 里写明的数值或要素，与原文一致；混合编码的文件名、属性能检索到。
   - **NDB**：遮蔽值（`-`）为 null 且注记原样；检索「10未満」能命中 A1 表头并 `cite` 到 `cell=A1`。
   - **ABR**：町字代码前导零保留（如 `0003000`），按全国地方公共团体コード能查到。
   - **跨来源**：一条 `query_sql` 同时读 e-Stat API 和经济普查（或 NDB）的观测值，每行出处各自指向自己的来源和快照，不串表。
   - 每个来源的每条检索结果都能 `cite` 还原到快照；`version` 报当前代次。
   - **泄漏检查**：跑完后读遍 `tmp_path` 下所有文件的字节和捕获的日志，断言不含测试用的假 appId。
2. 注册层：`rikolto registry export` 的结果与已提交的 `catalog/` 一致（五个来源都在）。
3. `summary.md` 写给人的**真实抓取清单**（只写命令和要核对的项目，不写任何值）：
   - 在一个新的临时数据目录里（`--data-dir`）依次 `rikolto crawl <来源>`、`rikolto parse`（命令名以当前 CLI 为准，`rikolto --help` 核对），五个来源各一遍；e-Stat 需要人先在自己的 shell 里设 `ESTAT_APP_ID`。
   - 每个来源要记的：请求数、字节数、快照数、观测值或文档条数、失败数、robots 是否放行；e-Stat 三张表各自 `TOTAL_NUMBER` 是否小于单次上限（不触发多页拒绝）。
   - 抽查：每个来源一条 `rikolto sql` 和一条 `rikolto search`，与 e-Stat 网页 / 原文件对照；确认数据目录里 `grep` 不到 appId。
   - 记录位置：`docs/acceptance/v0.3.0-maple-m2.md`（由主对话写，本任务不写 `docs/`）。
4. （固定要求）集成测试**不能写入仓库里受 git 跟踪的路径**（包括 `results/`）：证据默认写到临时目录，只有设置了环境变量 `DATUMOJ_EVIDENCE_DIR` 时才写到该目录（参考 `tests/integration/test_law_e2e.py` 的写法）。
5. `make lint` 0 条，`make check T=tests` 全部通过；全量测试要跑完，不要中途中断。

# 不做
- 不改各来源的采集器、解析器、yaml 和 `catalog/`。
- 不做真实联网抓取，不打开来源开关；真实抓取由人做。
- 不做欧美来源和多语言评测（属于 903）。
