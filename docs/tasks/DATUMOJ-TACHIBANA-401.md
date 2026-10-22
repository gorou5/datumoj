---
id: DATUMOJ-TACHIBANA-401
title: BODIK：复用 CKAN 采集器，加上 DataStore 分页读取
project: DATUMOJ
status: todo
priority: 2
depends_on: []
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/crawlers/ckan.py", "sources/jp-bodik.yaml", "tests/crawlers/test_ckan_datastore.py", "results/DATUMOJ-TACHIBANA-401/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/crawlers/test_ckan.py"]
acceptance:
  automated:
    - cmd: make check T=tests/crawlers/test_ckan_datastore.py
    - cmd: make check T=tests/crawlers
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-401/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
BODIK ODCS（自治体开放数据）的目录能用 IVY 的 CKAN 采集器抓下来；开了 DataStore 的资源能按页读出全部记录。

# 背景
开发线：D 目录线。计划第 5 节。IVY 的通用 CKAN 采集器在 `crawlers/ckan.py`（e-Gov 数据门户在用，配置见 `sources/jp-egov-data.yaml`）。样本在 `tests/fixtures/bodik/`（H1 录制，说明见 README）：`package_search` 两页（`rows=3`）；读谷村「公共施設」的 DataStore 资源（`total=67`）的 `datastore_search` 前三页（`limit=5`）、最后一页（`offset=65`，只有 2 条）和超出末尾的空页（`offset=67`）。实测要点：API 地址 `https://data.bodik.jp/api/3/action/`；`status_show` 返回 403，不能用来做健康检查；`datastore_active` 不能用于 Solr 过滤，只能看 `resources[].datastore_active`；空页的 `_links.next` 仍然给出下一个 offset，**不能靠 `_links.next` 判断结束**；许可在数据集级（`license_id`），资源级为 null（与 e-Gov 相反）；源数据里有重复记录和明显错误的坐标，原样保留。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-401/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. `sources/jp-bodik.yaml`：`kind: ckan`，`base_url` 为 BODIK 的 CKAN action API（以 H1 README 记下的实测地址为准），限速 0.5 请求/秒，`schedule: daily`，许可 `unverified` / `internal_only`（资源级许可记在快照 meta，多数是 CC BY 4.0，逐个核对前不改为可再分发），容量上限与 e-Gov 相同（单文件 200MB、总额 10GB），默认关闭。
2. CKAN 采集器加可选的 `scope.datastore: true`：资源 `datastore_active` 为 true 时，生成 `datastore_search` 任务（`resource_id`、`limit`（默认 1000）、`offset`、按 `_id` 升序排序），meta 写 `role=datastore`、`resource_url`、`dataset_id`。
3. 分页：下一页的 `offset` 按**实际返回的记录数**推进（不能按 `limit` 推进）；返回记录少于 `limit`、为 0，或 `offset` 达到 `total` 时停止；`scope.datastore_max_rows`（默认 100000）封顶，超出时停止并在 warning 里说明。
4. `datastore: false`（默认）时采集行为与现在完全一样，e-Gov 的现有测试全部通过。
5. 测试用 `pytest-httpserver` 回放样本，覆盖：目录分页、DataStore 各页拼接（最后一页不足 `limit` 时停止、共 67 条）、空页停止且不跟随 `_links.next`、返回条数少于 `limit` 但未到 `total` 时继续（可用样本改造出的本地响应）、封顶；数据集级许可写进快照 meta。

# 不做
- DataStore 记录不转成观测值，也不改目录入库状态（只作为原始快照保存）。
- 不改目录解析器和目录库。
