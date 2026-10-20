---
id: DATUMOJ-IVY-402
title: 目录解析、目录存储与目录检索
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-002"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/parsers/ckan_meta.py", "src/datumoj/store/catalog.py", "tests/store/test_catalog.py", "results/DATUMOJ-IVY-402/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: /opt/homebrew/bin/uv run --frozen pytest -q tests/store/test_catalog.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-402/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
CKAN 数据集元数据进入目录库，可以按关键词、组织、格式、更新时间检索，并标明每个资源是否已抓取正文。

# 背景
开发线：D 目录线。用户要先能“找到有哪些数据”。回答时要区分“目录查到”和“正文已读到”。直接用 `tests/fixtures/ckan_egov/` 的 package JSON 测试，不需要等采集器。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。不要 import 其他开发线的实现模块（集成任务除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-402/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. 解析器插件：package JSON → Dataset + Resource（标题、说明、标签、组织、地区、更新频率、两级许可原样保存、资源格式和 URL）。
2. 实现 `CatalogStore` 协议（SQLite）：upsert、按 id 取、列出资源及下载状态。
3. `search_catalog(query, filters, k)`：FTS5 trigram 检索标题、说明、标签；过滤来源、组织、格式、更新时间；结果标注“仅目录 / 已抓取正文 / 已入观测值库”。
4. 继承 `CatalogStoreSuite` 跑合格测试，再补转换、检索、过滤、许可缺失或矛盾时原样保留。

# 不做
- 不改 scope 以外的文件。
