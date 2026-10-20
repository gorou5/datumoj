---
id: DATUMOJ-IVY-203
title: JMA 长期 feed 采集器与解析器
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-002", "DATUMOJ-IVY-003", "DATUMOJ-IVY-104"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/crawlers/jma_feed.py", "src/datumoj/parsers/jma_xml.py", "sources/jp-jma.yaml", "tests/crawlers/test_jma_feed.py", "results/DATUMOJ-IVY-203/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/crawlers/test_jma_feed.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-203/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
气象厅防灾信息 XML 的长期 feed 能轮询，新条目都保存下来并转成可检索的 Document。

# 背景
开发线：B 文档线。长期 feed 每小时更新，只保留约 7 天，所以要尽早开始持续轮询（PLAN 第 12 节）。样本在 `tests/fixtures/jma/`。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。不要 import 其他开发线的实现模块（集成任务除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-203/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. 采集器插件 `kind=jma_feed`：解析 Atom feed，按 entry id 去重，只抓新条目；feed 本身也存快照。
2. 解析器插件：条目 XML 安全解析；可选字段（如 `Duration`）缺失不报错；每个条目一个 Document（标题、发表时间、官署、对象地区、正文），locator `entry=<id>`，`published_at` 必填。
3. 新增 `sources/jp-jma.yaml`（每小时，`scope.feeds` 列出要轮询的 feed）。
4. 测试：去重、新条目识别、可选字段缺失、恶意 XML 被拒。
5. （H1 实测）长期 feed `regular_l.xml` 是固定条数（约 9383 条）的滑动窗口，约每小时第 51 分更新一次；两次快照大小和条数可能完全相同，判断有无更新只能比 `updated` 和 entry id，不能看大小或条数。feed 里 `<link rel="self">` 写的是 `regular.xml`，`<id>` 每次不同，都不能当作去重依据。样本 snapshot1/snapshot2 之间新增 6 条、滚出 6 条，详见 `tests/fixtures/jma/README.md`。VPFG50、VPWP50 没有 `TargetDuration`、`ValidDateTime`，部分元素为空，解析器不能报错。
6. （契约二对齐）实现 `Crawler` 和 `Parser` 协议，测试用 `drive_crawler` 驱动；`chunk_id` 用 `<doc_key>#<locator>`。

# 不做
- 不做实时预报推送、不抓平年值。
