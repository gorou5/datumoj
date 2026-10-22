---
id: DATUMOJ-TACHIBANA-601
title: 变化检测：解析后生成变化事件，写进变化日志
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-001", "DATUMOJ-TACHIBANA-103"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/store/changes.py", "src/datumoj/changes.py", "src/datumoj/pipeline.py", "src/datumoj/wiring.py", "tests/store/test_changes.py", "tests/test_change_detection.py", "results/DATUMOJ-TACHIBANA-601/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/test_pipeline.py", "tests/test_pipeline_retry.py", "tests/test_pipeline_vectors.py", "tests/test_pipeline_content_state.py"]
acceptance:
  automated:
    - cmd: make check T=tests/store/test_changes.py
    - cmd: make check T=tests/test_change_detection.py
    - cmd: make check T=tests/test_pipeline.py
    - cmd: make check T=tests/test_pipeline_retry.py
    - cmd: make check T=tests/test_pipeline_vectors.py
    - cmd: make check T=tests/test_pipeline_content_state.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-601/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
每次解析之后，资料库能说出“哪份文档新增了、修改了、删除了，哪些抓取或解析失败了”，记录在变化日志里，每条都能引用到快照。

# 背景
开发线：F 入口线。计划 6.4 节。契约 001 定义了 `ChangeEvent`、`ChangeLog`、`ChangeLogSuite`、`classify_failure`。流水线在 `pipeline.py`（`parse_pending` → `_process` → `_dispatch`），接线在 `wiring.py`（103 已把数据位置收拢到存储配置）。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-601/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. `store/changes.py`：`SqliteChangeLog` 实现 `ChangeLog`，库文件 `<data_dir>/changes.sqlite`，通过 `ChangeLogSuite`；在 `wiring.build` 里接上（可被 overrides 替换）。
2. `changes.py`：纯函数 `detect(old_doc, old_text, new_doc, new_text) -> ChangeEvent | None` 等，判断规则：同一 `doc_key` 第一次出现 → `added`；法令按 `Document.metadata` 里的修订 ID（`law_revision_id` 等）不同判为 `modified`，没有修订 ID 时按正文哈希；网页和 PDF 按规范化正文的差异比例（`difflib` 相似度低于 0.98 才算修改，常量可调），只是导航或日期变化不算；统计表按快照 sha256 不同判为 `modified`。`summary` 写一行人能读的说明（例如“第 3 节文字变化约 12%”）。
3. 流水线在每份文档写入后调用检测，事件写进变化日志；检测出错只记 warning，不能让解析失败。
4. 失败事件：解析失败（`ParseRecord` 为 failed）→ `parse_failed`；抓取失败（manifest 里该资源最近一次是失败）→ `fetch_failed`，`summary` 里带 `classify_failure` 的类型；曾经成功、现在返回 404 / 410 的资源 → 它对应的文档记 `removed`。
5. 幂等：同一快照重复解析不产生重复事件（`event_id` 稳定）。
6. 新测试覆盖每种事件和阈值边界；`tests/test_pipeline*.py` 必须全部通过。

# 不做
- 不写报告和 MCP 工具（602、603）。
- 不回溯 IVY 已入库数据的历史变化（只从本任务之后的解析开始记录）。
