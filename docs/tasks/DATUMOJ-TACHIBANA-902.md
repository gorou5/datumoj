---
id: DATUMOJ-TACHIBANA-902
title: 检查点 m2 集成：BODIK、目录与 e-Stat 检索、关键词门槛、存储适配层、注册层导出
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-901", "DATUMOJ-TACHIBANA-301", "DATUMOJ-TACHIBANA-401", "DATUMOJ-TACHIBANA-402", "DATUMOJ-TACHIBANA-403", "DATUMOJ-TACHIBANA-501", "DATUMOJ-TACHIBANA-103"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/wiring.py", "src/datumoj/cli.py", "sources/**", "tests/integration/test_tachibana_m2.py", "results/DATUMOJ-TACHIBANA-902/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/integration/test_tachibana_m2.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-902/summary.md 完成说明"]
  review: {by: human, checklist: ["是否只改了接线和 sources", "S3 模式是否只连 moto", "数值是否与原表一致", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
m2 的各项能力用真实实现一起跑通，并且整条流水线在原始文件放在 S3（模拟 R2）上时同样能跑。

# 背景
开发线：集成。TACHIBANA 检查点 m2 的集成门槛。只允许改接线和 `sources/`。

本任务合并并通过人工验收后，打 tag `v0.2.0-tachibana-m2`。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-902/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. BODIK：回放 `tests/fixtures/bodik/`（`limit=5`，已录 offset 0/5/10/65/67，见该目录 README），目录入库；DataStore 回放的各页都成为快照，读到末页（不足 `limit`）后停止，不跟随 `_links.next`。
2. e-Stat：回放 `tests/fixtures/estat_files/`，`search_catalog` 能按表题找到统计表，结果出处能用 `cite` 还原，`query_sql` 取到的数值与原表一致。
3. 目录：e-Gov 目录分页样本解析后，`search_catalog` 的每条结果都有出处并能 `cite` 还原。
4. 关键词门槛：IVY 的评测门槛（`tests/integration/test_full_eval.py` 的同一套指标）全部达标，behavior 里的零命中题判为 `zero_hits`。
5. 存储适配层：设置 `DATUMOJ_RAW_BACKEND=s3` 并指向 moto 本地端点，跑一遍法令样本的 crawl → parse → index → search → cite，结果与本机 raw 一致。
6. 注册层：`datumoj registry export` 的结果与已提交的 `catalog/` 一致并通过 `tests/validate_schemas.py`。
7. （固定要求）集成测试**不能写入仓库里受 git 跟踪的路径**（包括 `results/`）：评测报告等证据默认写到临时目录，只有设置了环境变量 `DATUMOJ_EVIDENCE_DIR` 时才写到该目录（参考 `tests/integration/test_law_e2e.py` 的写法）。
8. 全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不改各开发线的模块。
