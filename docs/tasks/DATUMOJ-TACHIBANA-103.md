---
id: DATUMOJ-TACHIBANA-103
title: 存储适配层：一处存储配置、RawStore 的 S3 兼容（R2）实现、网络存储 ADR
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-001"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/storage.py", "src/datumoj/core/rawstore_s3.py", "src/datumoj/core/rawstore.py", "src/datumoj/wiring.py", "src/datumoj/cli.py", "src/datumoj/analysis/tools.py", "tests/core/test_storage.py", "tests/core/test_rawstore_s3.py", "docs/adr/0002-network-storage.md", "results/DATUMOJ-TACHIBANA-103/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/PLAN.md", "docs/TASKS.md", "docs/plans/**", "docs/tasks/**", "docs/acceptance/**", "docs/references/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_storage.py
    - cmd: make check T=tests/core/test_rawstore_s3.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-103/summary.md 完成说明"]
  review: {by: human, checklist: ["什么都不设时行为是否和现在完全一样", "S3 实现是否通过 RawStoreSuite 且只连 moto", "put 是否流式、是否去重", "凭证是否不进配置对象、日志和异常", "ADR 是否给出可选做法、代价和建议", "全部已有测试是否通过", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
数据放在哪里只由一处存储配置决定；原始文件（raw）可以放进 S3 兼容的对象存储（Cloudflare R2），本机目录仍是默认值。SQLite 类的库怎么上网络存储有一份 ADR。

# 背景
开发线：A 抓取基础。团队方向：运行时以后落到网络存储，云端优先 Cloudflare（`docs/PLAN.md` 第 4.4 节，计划 6.6 节）。现在 `cli.py`、`wiring.py`（`build_tools`）、`analysis/tools.py`、`core/rawstore.py` 各自读 `DATUMOJ_DATA_DIR`；`wiring.build` 按 `data_dir` 组装 manifest、chunk、关键词、目录、观测值、向量。`boto3`（运行时）和 `moto[s3,server]`（测试）已加进依赖。凭证变量名沿用团队的 `.env.example`：`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`、`R2_ENDPOINT`。契约 001 已把 `path_for` 从 `RawStore` 协议里去掉。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-103/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. `core/storage.py`：`StorageConfig`（pydantic）含 `data_dir`（本机目录，放 SQLite、LanceDB、Parquet、锁和运行记录）、`raw_backend`（`local` / `s3`）、`s3_endpoint`、`s3_bucket`、`s3_prefix`（默认空）、`s3_region`（默认 `auto`）。`load_storage_config(env=os.environ, data_dir=None)`：`data_dir` 参数优先，其次 `DATUMOJ_DATA_DIR`，默认 `./data`；`DATUMOJ_RAW_BACKEND`（默认 `local`）；`s3` 时读 `DATUMOJ_S3_BUCKET`、`R2_ENDPOINT`（或 `DATUMOJ_S3_ENDPOINT`）、`DATUMOJ_S3_PREFIX`，缺必填项报清楚的错。凭证**不放进** `StorageConfig`，只在建客户端时从环境变量读（`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`）。
2. 所有直接读 `DATUMOJ_DATA_DIR` 的地方（上面四处）改为经 `load_storage_config`；什么都不设时行为和现在完全一样（`./data`、本机 raw）。
3. `core/rawstore_s3.py`：`S3RawStore` 实现 `RawStore` 协议。对象键 `<prefix>/raw/<sha 前两位>/<sha>`；`put` 边读边算 sha256（先写临时文件，不把大文件整块读进内存），已存在就不再上传（内容寻址去重）；`get` / `open` 取回后校验 sha256，不符抛 `CorruptedBlob`；不存在抛 `NotFoundError`；`exists` 用 HEAD；`verify` 重新计算校验。
4. `S3RawStore` 通过 `RawStoreSuite` 全部合格测试：测试用 moto 的 `ThreadedMotoServer` 起本地端点，以 `endpoint_url` 方式连接（与 R2 的用法一致），不连任何真实服务。
5. `wiring.build` 按 `StorageConfig.raw_backend` 选 `FileRawStore` 或 `S3RawStore`；其他库仍按 `data_dir` 放本机。`datumoj` 各命令的参数和输出不变。
6. 凭证安全：日志、异常信息、`repr` 里不出现密钥（测试断言：故意给错凭证时报错文本不含密钥值）；缺凭证时报“缺少 R2_ACCESS_KEY_ID”这类清楚的错。
7. ADR `docs/adr/0002-network-storage.md`（状态写“提议”，由人定）：manifest、chunk、关键词、目录等 SQLite 库上网络存储的可选做法（迁到 D1；本机运行、定期把库文件备份到 R2；其他），LanceDB 和 Parquet / DuckDB 直接读写对象存储的可行性，各自的代价，以及建议。只写结论和依据，不迁移。
8. 新测试覆盖配置加载（默认值、环境变量、参数优先、缺项报错）、S3 合格测试、大文件流式写入（例如 20MB 不整块读入）、凭证不泄露。全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不连接真实 R2，不创建任何云资源，不做部署。
- 不迁移 SQLite、LanceDB、Parquet 的位置。
- 不改 `.env.example`。
