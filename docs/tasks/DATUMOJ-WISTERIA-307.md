---
id: DATUMOJ-WISTERIA-307
title: 观测值历史与代次副本的磁盘增长：历史按段只追加、当前查询不碰历史、代次副本对不可变文件用硬链接
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-WISTERIA-301"]
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 4, api: [], port: 0}
scope: ["src/datumoj/rikolto/store/observations.py", "src/datumoj/rikolto/wiring.py", "docs/adr/0007-observation-releases.md", "tests/store/test_observation_releases.py", "tests/regressions/test_audit_wisteria_307.py", "results/DATUMOJ-WISTERIA-307/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/PLAN.md", "docs/plans/**", "sources/**", "schemas/**", "src/datumoj/eltiro/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**", "src/datumoj/rikolto/analysis/**", "src/datumoj/rikolto/mcp_server.py", "src/datumoj/rikolto/cli.py"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/models.py", "src/datumoj/rikolto/core/contracts.py", "src/datumoj/rikolto/testing/**", "schemas/**", "tests/regressions/test_audit_wisteria_301.py"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_307.py
    - cmd: make check T=tests/regressions/test_audit_wisteria_301.py
    - cmd: make check T=tests/store
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-307/summary.md 完成说明（含新的历史布局、迁移步骤、写入 N 次后的磁盘占用对比、代次副本里硬链接和复制各占多少）"]
  review: {by: human, checklist: ["每次写入是否只追加新事件、不再重写整份历史", "301 格式的旧历史迁移后是否一条事件不丢、迁移可重复执行", "删除的旧文件是否只限于内容已被新段完整包含的 301 历史文件", "不带 token 的当前查询是否不打开历史段", "代次副本对 raw 和历史段是否用硬链接、跨文件系统时是否退回复制并记录", "相同内容的存储副本是否和上一个代次共用", "依赖被改动后查询是否仍报错", "301 的回归测试是否原样通过", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
修掉 301 实现带来的两处磁盘增长：
- 观测值历史每写一次就生成一份新的完整历史文件，旧文件不删，占用按写入次数的平方增长；
- 每保留一个代次，就把全部存储和全部 raw 真复制一份。

改完后：历史占用只随事件数线性增长；代次副本里不可变的文件不重复占盘。查询语义、契约和 301 的全部行为都不变。

# 背景
开发线：C。301（已合并进 `feature/0.4`）的实现在 `rikolto/store/observations.py`：
- `_write_table` 和 `import_release` 每次都把"旧历史 + 新事件"写成一个新的不可变历史文件（文件头带 `datumoj.history_only` 标记），current 文件头用 `datumoj.history_file` 指向它；上一个历史文件留在原处。
- 当前查询（`_connect` → `_scan`）每次都读 `parquet/*/*.parquet` 下每个文件的文件头，包括这些历史文件，靠标记跳过。
- `retain_generation` 用 `shutil.copyfile` 复制代次的每个存储副本和每个 raw 快照，`verified.json` 记 `storage: copy`。调用方在 `wiring.py`：push、restore、pull。

契约见 `docs/adr/0007-observation-releases.md` 和 `rikolto/core/contracts.py`。2026-10-22 的决定是"DATUMOJ 不做数据保留和清理"：本任务**不加**按时间或个数删除代次、删除历史的机制。只做两件事：不再产生冗余的副本；把已经产生的、内容被完整包含的冗余文件去掉。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- **契约不改**：`core/` 和 `testing/` 只读；实现对不上契约时写进 `summary.md` 并停下来问。
- 本机定时抓取用的就是这个检出，所以本机数据目录里可能已经有 301 格式的历史文件。迁移必须能处理它们。
- 回归测试写在 `tests/regressions/test_audit_wisteria_307.py`；`test_audit_wisteria_301.py` 不许改，必须原样通过。调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **历史按段只追加**：
   - 每次写入（`write_table`、`import_release`）只把本次的新事件写成一个不可变的历史段文件，不再重写旧历史。
   - 历史段放在 current 之外的独立目录（例如 `parquet-history/<source_id>/<表哈希>/`），文件名不可预测、不覆盖已有文件。
   - 一张表的完整历史等于它所有段的并集。去重、排序、截至查询的语义和 301 完全相同。
   - 写入的原子性照旧：current 替换失败时，本次新写的段要删掉，或者不被引用。
2. **当前查询不碰历史**：不带 token 的当前查询只打开 current 文件，不列举、不打开历史段。历史段只在截至查询、保留代次、发布索引时读取。
3. **迁移 301 格式**：
   - 遇到 301 格式的表（current 文件头带 `datumoj.history_file`），把它指向的那份历史文件转成历史段。
   - 先核对新段里的事件和原文件完全一致（逐条比较完整身份和排序键），再删掉同目录里所有带 `datumoj.history_only` 标记、内容已被新段完整包含的旧历史文件。核对不一致就停止，不删任何东西，报错。
   - 迁移可重复执行；中途失败重跑，结果一样。
   - 迁移在打开观测值库写入时自动做，也提供一个只读的检查函数，报告有多少表待迁移、能释放多少空间。
   - 只删上面这一种文件，其他任何文件都不删。
4. **代次副本用硬链接**：
   - `retain_generation` 对 raw 快照和历史段这类内容寻址、不可变的文件用硬链接。
   - 硬链接失败（跨文件系统等）就退回复制，并在 `verified.json` 里逐个文件记下是 `link` 还是 `copy`。
   - SQLite 库、current 文件这类可变文件仍然复制，不链接到在线库。
   - 和上一个已保留代次哈希相同的存储副本，链接到上一个代次里的那份，不再复制。
   - 查询时的轻量核对（大小和修改时间）照旧，硬链接的文件被改动后查询仍要报错。
5. **ADR 0007**：实现说明改成新的历史布局、迁移规则和代次副本的存储方式。写明"不做按时间或个数的清理，按 2026-10-22 的决定由另外的机制处理"。
6. **回归测试** `test_audit_wisteria_307.py` 覆盖：
   - 同一张表写入 N 次（N ≥ 20）后，历史占用随事件数线性增长：所有历史文件大小之和不超过"段数 × 单次段大小"再加常数，不出现整份历史的重复；
   - 不带 token 的当前查询不打开任何历史段（用记录文件打开的方式断言）；
   - 截至查询、固定代次查询的结果和 301 的实现完全相同（对同一组写入序列逐行比较）；
   - 301 格式的表迁移后事件一条不丢，冗余的旧历史文件被删掉，其他文件都在；
   - 迁移中途失败后重跑，结果正确；
   - 核对不一致时不删任何文件；
   - 连续保留两个代次，raw 和历史段在两个代次之间、以及和在线目录之间共用同一份文件（同一个 inode）；
   - 硬链接失败时退回复制并记录；
   - 硬链接的 raw 或历史段被改动后，固定代次查询报 `GenerationUnavailable`。
7. `make check T=tests` 全部通过，包括 `test_audit_wisteria_301.py`。

# 不做
- 不加按时间或个数删除代次、历史、raw 的机制。
- 不改查询语义、契约、MCP 和命令行。
- 不改 Eltiro 的代码。
