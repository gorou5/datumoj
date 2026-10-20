---
id: DATUMOJ-IVY-002
title: 契约二：接口协议、插件自动发现、假实现与契约测试
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-IVY-001"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/core/test_contracts.py", "results/DATUMOJ-IVY-002/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**"]
acceptance:
  automated:
    - cmd: /opt/homebrew/bin/uv run --frozen pytest -q tests/core/test_contracts.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-002/summary.md 完成说明"]
  review: {by: human, checklist: ["协议是否足够让各线独立开发", "合格测试是否真的约束了行为而不只是签名", "插件发现是否会因为一个坏模块拖垮全部（应报清楚的错误）", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
每个模块的接口、假实现和“合格测试”都先写好。各线的真实实现只要通过同一套合格测试，就能直接替换假实现。

# 背景
开发线：契约。并行开发的第二块基石。插件自动发现让采集器和解析器不用改任何共享的登记文件，多条线同时开发也不会冲突。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。不要 import 其他开发线的实现模块（集成任务除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-002/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. `contracts.py` 用 `typing.Protocol` 定义：`RawStore`、`Manifest`、`HttpFetcher`、`Crawler`、`Parser`、`ChunkStore`、`KeywordIndex`、`VectorIndex`、`Embedder`、`Retriever`、`CatalogStore`、`ObservationStore`、`Registry`。每个方法写清输入输出类型和错误约定（docstring）。
2. `Parser.parse(snapshot, data: bytes) -> ParseResult`；`ParseResult` 含 `documents`、`chunks`、`observations`、`datasets`、`resources`、`warnings`。
3. **插件自动发现**：`discover_crawlers()` 扫描 `datumoj.crawlers` 包下的所有模块，收集声明了 `kind` 的 `Crawler` 子类；`discover_parsers()` 扫描 `datumoj.parsers`，按 `(source_kind, content_type)` 匹配。同一个键有两个实现时报错。新增采集器或解析器只需要新建文件。
4. `src/datumoj/testing/fakes.py`：每个协议一个内存版假实现（FakeRawStore、FakeManifest、FakeChunkStore、FakeRetriever 等），行为符合协议。
5. `src/datumoj/testing/contract_suites.py`：每个协议一套可复用的合格测试（如 `class RawStoreSuite`：实现方继承它并提供 fixture 即可运行）。假实现必须通过全部合格测试。
6. `tests/core/test_contracts.py`：让每个假实现跑一遍合格测试；测试插件发现（用 tmp 包模拟两个插件和一个重复键）。

# 不做
- 不写任何真实实现。
