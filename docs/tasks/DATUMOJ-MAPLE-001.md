---
id: DATUMOJ-MAPLE-001
title: 契约修订：状态代次清单、对象存储条件写入接口、写入租约；ADR 0005
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/core/test_contracts_state.py", "schemas/*.schema.json", "docs/adr/0005-state-generations.md", "results/DATUMOJ-MAPLE-001/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example", "docs/PLAN.md", "docs/TASKS.md", "docs/plans/**", "docs/tasks/**", "docs/acceptance/**", "docs/references/**"]
protected: ["tests/fixtures/**", "schemas/registry/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_contracts_state.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-001/summary.md 完成说明"]
  review: {by: human, checklist: ["代次清单是否够恢复和还原出处、又没有多余字段", "有效代次和发布规则是否能防止半截代次和两个写入者", "租约规则是否只允许接管过期租约", "是否改了已有模型", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
“R2 上的一份完整状态”有了明确的契约：代次清单里有什么、怎样才算有效代次、谁能写、怎样原子地发布。后面的推送、拉取、租约、Actions 工作流都按这份契约实现。

# 背景
开发线：契约。ADR 0002 已定：raw、各库、观测值、向量都上 R2，本机是可重建的工作副本；同步频率按来源设置。0.2 只做了 raw 的 S3 适配器（`core/rawstore_s3.py`）。m0 的 107 已有本机的发布点（`publish_current` 和 staged / published / failed），状态代次是它在 R2 上的延伸：代次只包含已发布的状态。本地数据目录现有：`manifest.sqlite`、`chunks.sqlite`、`keyword.sqlite`、`catalog.sqlite`、`changes.sqlite`、`observations.duckdb` + `parquet/`、`vectors/`（LanceDB）+ `vectors-state.json`、`runs.jsonl`、`control.json`、`raw/`、`locks/`、`logs/`。向量怎么上 R2 由 101 的验证决定，本契约只记录向量覆盖，不规定向量的存法。

计划见 `docs/plans/0.3-maple.md`（第 6.1–6.5 节是云端运行的设计，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。检查点 m0（证据正确性）已完成。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。存储边界见 `docs/adr/0002-network-storage.md`，证据身份见 `docs/adr/0004-evidence-identity.md`，状态代次见 `docs/adr/0005-state-generations.md`（由 MAPLE-001 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 / R2 一律用 moto（锁定的 moto 5.2.4 支持 `IfNoneMatch` / `IfMatch` 条件写入）。不连真实 R2。
- R2 凭证只从环境变量 `R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY` 读，endpoint、bucket 沿用 `core/storage.py` 的 `StorageConfig`（`R2_ENDPOINT`、`DATUMOJ_S3_BUCKET`、`DATUMOJ_S3_PREFIX`）。**仓库和 Actions 日志都是公开的**：命令输出、日志、异常信息里不出现凭证、endpoint、bucket 名和原始数据内容，只写计数、来源 ID、代次号和对象键的相对部分。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-001/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. 模型（`models.py`，全部新增、不改已有模型）：`StoreCopy`（逻辑名如 `manifest` / `chunks`、对象键、SHA256、字节数、格式）；`StateGeneration`（代次号 ≥1、上一代次号、生成时间、写入者 `{kind: actions | local, run_id}`、`stores: list[StoreCopy]`、raw 前缀、向量覆盖 `{model, version, dim, embedded_chunks, pending_chunks}` 或空、各来源最后成功运行 `{source_id: {run_id, finished_at}}`）；`WriteLease`（持有者、令牌、取得时间、到期时间）。都有 JSON Schema（`schemas/`，沿用已有 schema 的写法）。
2. 接口（`contracts.py`）：`StateObjectStore` 协议——`get(key) -> (bytes, etag) | None`、`put(key, data)`、`put_if_absent(key, data) -> etag`、`put_if_match(key, data, etag) -> etag`、`delete_if_match(key, etag)`、`exists(key)`、`list(prefix)`；条件不满足时抛统一的 `PreconditionFailed`（新异常）。`testing/` 里给内存假实现和契约合格测试套件（沿用 `contract_suites.py` 的写法），假实现必须通过它。
3. ADR `docs/adr/0005-state-generations.md` 写清：对象键布局（内容寻址的副本对象、代次清单 `generations/<n>.json`、指向当前有效代次的指针、租约对象）；**有效代次**的定义（全部副本已上传、清单已写、指针已用条件写入切到它；中途失败的不是有效代次）；发布规则（清单用 `put_if_absent`，指针用 `put_if_match`，只有代次号紧接当前有效代次时成功，被抢先则本轮作废）；租约规则（条件写入取得、续期、释放；`--force` 只能接管已过期的租约）；来源 `sources/*.yaml` 的 `schedule` 就是 Actions 上的抓取频率（本版本不另加状态上传频率字段，代次按每次运行发布）；代次里各库必须是同一时刻的一致副本（SQLite 用 backup API，不直接复制正在写的文件）。
4. 新测试 `tests/core/test_contracts_state.py`：模型的校验（代次号、时间顺序、覆盖数非负）、JSON 往返、假实现通过契约套件（含两个写入者竞争 `put_if_absent` / `put_if_match` 只有一个成功）。
5. 全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不写 S3 实现、推送、拉取、租约逻辑（102、104、106 做）。
- 不规定向量的存法（101 定）。
- 不改已有模型和接口。
