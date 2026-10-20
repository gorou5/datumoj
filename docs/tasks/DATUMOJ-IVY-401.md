---
id: DATUMOJ-IVY-401
title: 通用 CKAN 采集器（e-Gov データポータル）
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-002", "DATUMOJ-IVY-104"]
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/crawlers/ckan.py", "sources/jp-egov-data.yaml", "tests/crawlers/test_ckan.py", "results/DATUMOJ-IVY-401/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/crawlers/test_ckan.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-401/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
CKAN 门户的全部数据集元数据能抓下来并增量更新，资源文件按白名单下载。以后 BODIK 和欧美门户直接复用。

# 背景
开发线：D 目录线。接口说明：`/Users/aaron/codex_workspace/digital-assets/analysis/eGov_Data_Portal_Agent_Guide.md`。样本在 `tests/fixtures/ckan_egov/`。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块（例如 304 用 302 的地区码表）；其他任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-401/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. 采集器插件 `kind=ckan`：`package_search` 按 `rows/start` 分页，终止条件按实际返回数量判断。
2. 增量：`recently_changed_packages_activity_list`；不可用时退回全量并在报告里说明。
3. 资源白名单（`scope`）：格式、组织、单文件大小上限；不在白名单的只记元数据。新增 `sources/jp-egov-data.yaml`。
4. 测试：分页、增量、白名单、单个资源 404 不影响整体。
5. （H1 实测）e-Gov データポータル所有数据集的数据集级 `license_id` 都是空的，许可只能从资源级读取；`recently_changed_packages_activity_list` 没有总数字段，同一数据集会连续出现多次，要去重；登记的资源链接常失效（404，或 302 跳到 404 页），要记为抓取失败而不是成功。
6. （契约二对齐）实现 `Crawler` 协议，测试用 `drive_crawler` 驱动。

# 不做
- 不解析资源内容，不做 DataStore。
