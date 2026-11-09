---
id: DATUMOJ-MAPLE-148
title: cite 还原非 CKAN 目录条目和观测值行（entry=、row= 出处）
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/analysis/tools.py", "tests/analysis/**", "tests/regressions/test_audit_148.py"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "sources/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/index/**", "src/datumoj/rikolto/pipeline.py"]
protected: ["tests/fixtures/**", "eval/questions/**", "src/datumoj/rikolto/eval/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_148.py
    - cmd: make lint
    - cmd: make check T="tests --deselect=tests/integration/test_wisteria_m1.py::test_us_dollar_series_ranks_first_for_gdp_usd"
  evidence: ["测试输出日志", "变更文件清单", "各来源 entry=/row= 出处的还原结果表"]
  review: {by: other_family, checklist: ["eu-data-europa、fr-data-gouv 的 entry= 出处是否能还原", "是否从原始字节重新解析得到，而不是从目录库或观测值库直接取", "哈希校验和 not_found 的理由是否保留", "CKAN 原有路径是否没变", "是否越界"]}
  human: sampled
max_attempts: 4
---
# 目标
`cite` 能还原所有解析器给出的目录条目和观测值行出处。现在 `entry=N` 只在来源 `kind == "ckan"` 时处理，eu-data-europa（`data_europa_repo`）和 fr-data-gouv（`udata`）的目录条目都返回 `not_found`。

# 背景
MAPLE-903 第 3 次尝试的 MCP 测试失败：`cite eu-data-europa entry=40: not_found`、`cite fr-data-gouv entry=0: not_found`。`search_catalog` 返回的 citation 来自解析器给出的 `Dataset.snapshot_sha256` 和 `Dataset.locator`，所以出处本身是对的，缺的是 `cite` 这一侧。

`cite` 的通用路径是取原始字节、校验哈希、用对应解析器重新解析，再在 `result.chunks` 里按 locator 找。目录条目和观测值不在 `result.chunks` 里，在 `result.datasets` 和 `result.observations` 里。

任务分支从 `feature/0.4` 拉出，合并回它。

# 要求
1. 通用路径在 `result.chunks` 里找不到 locator 时，再到同一次重新解析的 `result.datasets`（按 `Dataset.locator`）和 `result.observations`（按 `Observation.locator`）里找。找到就返回 `status: ok`：
   - 目录条目的原文是标题、说明和标签；
   - 观测值的原文是这一行的字段。
   原文照现有写法包进 `SourceText`。
2. 原始字节校验、解析器选择、`not_found` 和 `ambiguous` 的行为不变。CKAN `package_search` 的现有专用路径不变。
3. 新增 `tests/regressions/test_audit_148.py`，用 `tests/fixtures/` 里已有的录制样本，经真实解析器入库后断言：
   - eu-data-europa `entry=40`、fr-data-gouv `entry=0` 能还原，原文含数据集标题；
   - eu-eurostat、intl-wdi 的 `row=` 出处能还原；
   - jp-census 的 `member=…;row=` 出处能还原；
   - 不存在的下标仍是 `not_found`，并给出理由；
   - jp-bodik（CKAN）原有用例结果不变。
4. 交付说明里列一张表：每个有目录条目或观测值的来源，取一条出处，写明还原结果。
5. `make lint` 0 条，全量测试全部通过、要跑完；全量命令排除现存的 1 个 xfail，不新增 xfail 或 skip。
6. 调度器里没有人能回答问题，不要等待回复。遇到范围缺口，在 scope 内选最小的做法完成，写进交付说明。

# 不做
- 不改解析器、索引和入库流程（检索投影在 MAPLE-149）。
- 不改夹具和题库，不连网络，不读本机 `data/`。
- 不写账号、密钥、本机绝对路径。
