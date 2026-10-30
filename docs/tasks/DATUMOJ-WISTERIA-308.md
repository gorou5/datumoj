---
id: DATUMOJ-WISTERIA-308
title: Rikolto 固定代次的只读视图：目录库和文档块库只读打开、一次打开同一代次的三个视图、契约加历次发布枚举
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-WISTERIA-301", "DATUMOJ-WISTERIA-307"]
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/rikolto/core/contracts.py", "src/datumoj/rikolto/core/models.py", "src/datumoj/rikolto/testing/**", "schemas/*.schema.json", "src/datumoj/rikolto/store/catalog.py", "src/datumoj/rikolto/store/chunks.py", "src/datumoj/rikolto/store/observations.py", "src/datumoj/rikolto/wiring.py", "docs/adr/0007-observation-releases.md", "tests/core/test_contracts.py", "tests/core/test_models.py", "tests/store/test_catalog.py", "tests/store/test_chunk_store.py", "tests/store/test_observation_releases.py", "tests/store/test_generation_views.py", "tests/regressions/test_audit_wisteria_308.py", "results/DATUMOJ-WISTERIA-308/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/PLAN.md", "docs/plans/**", "sources/**", "src/datumoj/eltiro/**", "src/datumoj/rikolto/analysis/**", "src/datumoj/rikolto/mcp_server.py", "src/datumoj/rikolto/cli.py", "src/datumoj/rikolto/core/state.py"]
protected: ["tests/fixtures/**", "tests/regressions/test_audit_wisteria_002.py", "tests/regressions/test_audit_wisteria_301.py", "tests/regressions/test_audit_wisteria_307.py"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_308.py
    - cmd: make check T=tests/regressions/test_audit_wisteria_002.py
    - cmd: make check T=tests/regressions/test_audit_wisteria_301.py
    - cmd: make check T=tests/regressions/test_audit_wisteria_307.py
    - cmd: make check T=tests/core
    - cmd: make check T=tests/store
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-308/summary.md 完成说明（含新增契约的签名和语义、只读打开怎样保证不写、同日多次发布的枚举示例）"]
  review: {by: human, checklist: ["只读打开是否不建表、不迁移、不建目录、不创建缺失的库、不进写事务", "只读视图的写方法是否一律报错", "三个视图是否来自同一代次、token 是否核对", "代次被改动或不可用时是否报错、不回退最新", "发布枚举是否能列出同日先后的每次发布、顺序和截至查询一致", "按枚举结果做截至查询是否不再有跨来源歧义", "新增内容是否只加不改、现有调用方和 002/301/307 的回归测试是否原样通过", "ADR 0007 是否更新", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
给 Eltiro 执行器（804）补上两个前置接口：
1. 一次拿到同一个已发布代次的三个只读视图：观测值、目录库、文档块库；
2. 在固定代次里列出历次发布，让 `revisions: all` 能取全同一天先后的多次发布。

# 背景
开发线：Rikolto。804 两次尝试都按停止条款停下，原因：
- `SqliteCatalogStore`、`SqliteChunkStore` 只有一种打开方式：建目录、建表，文档块库还会进写事务并升级版本号。没法拿它们只读地打开保留代次目录 `.observation-generations/<代次>/data/` 里的 `catalog.sqlite`、`chunks.sqlite`。
- `ObservationReleaseStore` 只有 `pin_generation`、`query_readonly`、`describe`，没有列出发布的接口。同一引用同一天先发 A 再发 B，查询只能看到 B，A 的发布标识推不出来。`store/observations.py` 内部的 `release_index` 视图已有这些信息（按来源、发布键、发布日、`SnapshotOrder`），但没在契约里公开。

相关的已完成实现：
- 002 的发布契约和 ADR 0007；
- 301 / 307 的代次保留：`retain_generation`、`verified.json` 记录文件大小和修改时间，查询时只做 stat 核对。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 本任务同时改契约和实现。契约**只加不改**：现有方法的签名和语义不变。
- 不改 Eltiro 的任何文件；804 会在本任务合并后从 main 重做。
- 不改 `core/state.py` 的状态推拉格式。
- 回归测试写在 `tests/regressions/test_audit_wisteria_308.py`；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **目录库和文档块库只读打开**：
   - 给 `SqliteCatalogStore`、`SqliteChunkStore` 各加一个只读打开方式（例如类方法 `open_readonly(path)`）。
   - 用 SQLite 的只读 URI 打开：不建目录、不建表、不迁移、不升级版本号、不进写事务；库文件不存在就报错，不创建。
   - schema 版本和代码要求的不一致时报错，不迁移。
   - 写方法一律抛错，不碰库文件。
   - 读方法（`get_dataset`、`search`、`get_document`、`get_chunk`、`iter_chunks` 等）和可写打开时结果一致。`wiring.py` 里 `_CatalogQueries` 的多词搜索在只读视图上也要生效。
   - 测试：只读文件（去掉写权限）能打开能读；缺失文件报错且不产生新文件；打开、读、关闭前后库文件的字节和修改时间不变。
2. **同一代次的三个视图**：在 `wiring.py` 加一个入口（例如 `open_generation_views(data_dir, generation_token=None)`）：
   - 不给 token 时用 `pin_generation()` 固定当前已发布代次；给了就严格用它。
   - 返回的观测值视图在每次查询时都带这个 token；目录库和文档块库从该代次保留目录只读打开。
   - 打开前按 `verified.json` 核对这两个库文件（和 301 / 307 对查询文件的核对同样只做 stat）；对不上就报 `GenerationUnavailable`，不回退最新代次，不读在线库。
   - token 无效报 `InvalidGenerationToken`。
   - 返回对象可以关闭（上下文管理器），关闭后释放全部连接。
3. **契约：历次发布枚举**（`core/contracts.py`，模型放 `core/models.py`）：
   - 在 `ObservationReleaseStore` 上加 `list_releases(*, generation_token, source_id, table_key=None)`，返回固定代次里该来源（可选限定到表）的全部发布，按截至查询用的同一排序键（发布日、`SnapshotOrder`）从早到晚排列。
   - 每一项是新模型（例如 `ReleaseEntry`），至少包含：`source_id`、发布键（有 `release_id` 用它，旧数据用 `snapshot_sha256`）、发布日、排序位置。同日多次发布各自成项。
   - `query_readonly` 的 `as_of_release` 额外接受这个模型：按它带的来源和排序位置定截至边界，因此不会再报跨来源的 `AmbiguousRelease`。原来的 `date`、`str` 用法不变。
   - token 规则和 `query_readonly` 一样：无效、代次不可用一律报错，不回退。
   - 只读本代次的发布索引和该表的历史段，不把全部历史载入内存，受同样的超时限制。
   - 契约合格测试（`testing/contract_suites.py`）和假实现（`testing/fakes.py`）同步补上。新模型要导出 JSON Schema 的，按现有做法加进 `schemas/`。
4. **ADR 0007** 补一节：发布枚举的语义、排序、和截至查询的对应关系、只读视图。
5. **回归测试** `test_audit_wisteria_308.py` 覆盖：
   - 只读打开不写库：字节、修改时间不变，没有新文件（含 `-wal`、`-journal`、`-shm`）；
   - 只读视图调写方法报错；
   - 缺失文件、schema 版本不符时报错且不创建、不迁移；
   - 三个视图来自同一代次；运行中发布新代次，视图仍读原代次；
   - 保留代次里的库文件被改动后打开报 `GenerationUnavailable`，不回退；token 无效报 `InvalidGenerationToken`；
   - 同日先 A 后 B 两次发布：`list_releases` 列出 A、B 且顺序正确；`as_of_release=<A 的项>` 取到 A 的值，`<B 的项>` 取到 B 的值；
   - 两个来源用同一个发布标识时，按枚举项截至查询不报歧义、结果分别正确；
   - 现有 `date` / `str` 截至查询结果不变。
6. `make check T=tests` 全部通过，002、301、307 的回归测试原样通过。

# 不做
- 不改 Eltiro，不做执行器（804）。
- 不改状态推拉格式和代次保留方式，不加清理（团队 2026-10-22 的决定）。
- 不给 MCP 和命令行加新工具。
