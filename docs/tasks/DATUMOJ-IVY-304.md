---
id: DATUMOJ-IVY-304
title: 统计 Dashboard 采集器、分批规划与观测值转换
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-104", "DATUMOJ-IVY-302"]
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/crawlers/stat_dashboard.py", "src/datumoj/parsers/stat_dashboard.py", "sources/jp-stat-dashboard.yaml", "tests/crawlers/test_stat_dashboard.py", "tests/fixtures/stat_dashboard/getIndicatorInfo_all.synthetic.json", "results/DATUMOJ-IVY-304/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/crawlers/test_stat_dashboard.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-304/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
统计 Dashboard 的指标目录和精选指标的全地区时间序列能抓下来并转成 Observation。

# 背景
开发线：C 统计线。Dashboard API 无需 Key；`getData` 每次最多 5 个指标、50 个地区，没有分页；`modifiedFrom` 可做增量。样本在 `tests/fixtures/stat_dashboard/`。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块（例如 304 用 302 的地区码表）；其他任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-304/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. 采集器插件 `kind=stat_dashboard`：指标目录全量；精选指标来自 `scope.indicators`。
2. 分批规划器（纯函数，单独测试）：指标 × 地区拆成每批 ≤5 指标、≤50 地区。
3. 解析器插件：响应 → Observation（地区码用 DATUMOJ-IVY-302 归一化，时间解析成 `time_start/time_end/granularity`，单位原样）；指标目录 → Dataset 记录。
4. 增量：用 `modifiedFrom`。
5. 新增 `sources/jp-stat-dashboard.yaml`（约 10 个精选指标：人口、世帯数、出生、死亡、転入転出、県内総生産、就業者数等，指标码从样本里取）。
6. 测试：规划器边界（正好 5/50、超出、空）、转换后的数值和样本逐条一致、地区码正确。
7. （H1 实测）带 `modifiedFrom` 的请求可能仍返回全部数据，增量逻辑不能假设结果一定变少。（契约对齐）解析器是无状态的（`parse(snapshot, data, meta)` 拿不到以前的解析结果），**不做跨快照去重**；同一张表重写时由 `ObservationStore.write_table` 整体替换，不会产生重复行。解析器只需保证同一份响应里不产生重复的 `obs_id`，并且 `obs_id` 由内容稳定生成（同一条记录两次解析得到同一个 obs_id）；响应里参数回显为 `ModifiedFrom`（大写开头）。部分指标在部分地区没有数据（样本 b2 的社会増減数只有全国），要原样表现为缺失，不能补零。
8. （审查多轮后明确）指标目录**全量**抓取：目录请求不带 `Category` 参数（或先取类别列表再逐类全部抓取），不能只抓 `sources` 里写死的几个类别；`scope.indicators` 只决定 getData 抓哪些指标，不限制目录。录制样本里只有 6 个按类别过滤的目录响应（录样本时为控制体积做的），没有不带过滤的全量响应：测试请新建一个人工构造的小样本 `tests/fixtures/stat_dashboard/getIndicatorInfo_all.synthetic.json`（结构照抄已有的 getIndicatorInfo 样本，几十条即可），用它验证全量请求和解析。
9. （契约二对齐）实现 `Crawler` 和 `Parser` 协议，测试用 `drive_crawler` 驱动；观测值经 `ParseResult.observations` 交出，指标目录经 `ParseResult.datasets` 交出。

# 不做
- 不改 scope 以外的文件。
