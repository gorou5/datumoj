---
id: DATUMOJ-TACHIBANA-301
title: e-Stat 统计表产出目录数据集，能被目录检索找到
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-001"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/parsers/estat_tables.py", "tests/parsers/test_estat_dataset.py", "results/DATUMOJ-TACHIBANA-301/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/crawlers/test_estat_files.py", "tests/integration/test_stats_e2e.py"]
acceptance:
  automated:
    - cmd: make check T=tests/parsers/test_estat_dataset.py
    - cmd: make check T=tests/crawlers/test_estat_files.py
    - cmd: make check T=tests/integration/test_stats_e2e.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-301/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
e-Stat 的每张统计表在产出观测值的同时进入目录，`search_catalog` 能按表题和调查名找到它，再用 `query_sql` 取数值。

# 背景
开发线：C 统计线。IVY 验收的遗留缺口（`docs/acceptance/v0.1.0-ivy.md` 第 4 节，计划 6.5 节）：e-Stat 表只能用 SQL 查到。解析器在 `parsers/estat_tables.py`，按来源配置里的表清单（`statInfId`、表题、调查名）解析；流水线 `_dispatch` 已会把 `ParseResult.datasets` / `resources` 写进目录库。样本沿用 `tests/fixtures/estat_files/`。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-301/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. 每张表产出一条 `Dataset`：`dataset_key` 与观测值相同（`<source_id>:<statInfId>`）；`kind = "stat_table"`；`title` 为清单表题；`description` 写调查名、统计期间（观测值的时间标签范围）和地区层级；`publisher` 取清单里的机关名（没有就留空，不编造）；`tags` 含调查名；`extras` 含 `stat_inf_id`、`survey`、`time_labels`（最早、最晚）、`area_level`；`url` 为快照的 `resource_url`。
2. `Dataset.snapshot_sha256` 为该快照，`locator` 指向表题所在单元格（`sheet=…;cell=…`，或 CSV 的 `row=…`），格式和观测值的 locator 一致，能用 `cite` 还原。
3. 同时产出一条 `Resource`（表文件本身：url、format、media_type），使目录库能把它的入库状态标为 `observations_loaded`（沿用流水线已有逻辑）。
4. 同一快照解析两次，Dataset 和 Resource 不变（键稳定）。
5. 新测试覆盖每条；e-Stat 解析器原有的测试 `tests/crawlers/test_estat_files.py` 和 `tests/integration/test_stats_e2e.py` 必须仍然通过（观测值不能有任何变化）。

# 不做
- 不改观测值的产出，不改目录库和流水线。
