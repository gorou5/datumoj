---
id: DATUMOJ-IVY-201
title: e-Gov 法令采集器
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-002", "DATUMOJ-IVY-104"]
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/crawlers/egov_law.py", "tests/crawlers/test_egov_law.py", "results/DATUMOJ-IVY-201/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/crawlers/test_egov_law.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-201/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
实现 `kind=egov_law` 的采集器插件：法令列表分页，生成每部法令现行版 XML 的抓取任务，支持增量和 LIMIT。

# 背景
开发线：B 文档线。接口说明：`/Users/aaron/codex_workspace/digital-assets/analysis/eGov_Law_API_v2_Agent_Guide.md`。样本在 `tests/fixtures/egov_law/`（见 README.md）。测试里用契约的假实现驱动采集器，不需要等执行器 DATUMOJ-IVY-105。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块（例如 304 用 302 的地区码表）；其他任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-201/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. `plan`：法令列表逐页抓取，为每部法令生成“现行版全文（XML 格式）”任务。
2. 纯函数 `extract_law_xml(raw_json_bytes) -> bytes`：从 JSON 里取出 base64 编码的 XML；损坏输入报清楚的错误。
3. 增量：比对列表里每部法令的修订信息和 manifest 里上次的记录，没变的不抓正文。
4. 时间口径：抓取现行版时记录“按哪天的施行状态”（施行基准日）；同时抓取修订历史，纯函数 `pending_amendments(revisions, asof)` 找出**已公布但在基准日尚未施行**的修改（附公布日和施行日）。这两项随快照一起保存，供解析器和回答使用。
5. `limit` 限制法令部数。
6. 测试：分页、增量跳过、单部 404 不影响其他、`extract_law_xml` 正常和损坏输入。
7. （H1 实测）分页结束只能按“本页实际返回条数为 0 或少于 limit”判断：超出末尾的空页 `total_count` 会变成 0，最后一页和空页都**没有** `next_offset` 键（不是 null）。
8. （H1 实测）判断现行版不能只看修订历史的状态字段：有的法令（公文書管理法、番号法）没有 `CurrentEnforced` 条目，最新施行版被标成 `PreviousEnforced`。要用施行日和 asof 比较。
9. （H1 实测）议员立法的法令 ID 是 `AC1` 形式（如 `428AC1000000103`），不能按 `AC0` 拼；`law_data` 的 404 错误码是 404004，`law_revisions` 是 404001，两种都要识别成“不存在”。
10. （契约二对齐）施行基准日和未施行修改放进 `FetchTask.meta`（由执行器存进 manifest，解析器经 `meta` 参数拿到），不要另建存储。测试用 `datumoj.testing.fakes.drive_crawler` 驱动，不必等 105。
11. （法令 meta 格式，201 与 202 共用）现行版全文任务的 `FetchTask.meta` 固定为 `{"role": "law_data", "law_id": "<法令ID>", "asof": "YYYY-MM-DD", "pending_amendments": [{"amendment_law_num": str|null, "promulgate_date": "YYYY-MM-DD"|null, "enforcement_date": "YYYY-MM-DD"|null, "enforcement_comment": str|null}]}`（以上是必有的键，可以有其他键，例如 `law_revision_id`、`amendment_law_title`；解析器只依赖必有的键）；没有未施行修改时 `pending_amendments` 为空列表；施行日未定（如“政令で定める日”）时 `enforcement_date` 为 null，原文放在 `enforcement_comment`。

# 不做
- 不解析法令结构（DATUMOJ-IVY-202）。
