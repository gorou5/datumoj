---
id: DATUMOJ-IVY-104
title: 来源注册表 schema 与校验
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-002"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/registry.py", "sources/jp-egov-law.yaml", "sources/README.md", "tests/core/test_registry.py", "results/DATUMOJ-IVY-104/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_registry.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-104/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
每个来源一个 `sources/<id>.yaml`，能校验并加载；抓取范围、限速、许可都从这里读。

# 背景
开发线：A 抓取基础。PLAN 第 5 节。注册表是防止无边界抓取的第一道闸。各来源的 yaml 由对应开发线的采集器任务新增。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。不要 import 其他开发线的实现模块（集成任务除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-104/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. 实现 `Registry` 协议。`SourceSpec`：`id`（`^[a-z]{2,4}(-[a-z0-9]+)+$`）、`name`、`country`、`kind`、`base_url`、`allowed_hosts`、`rate_per_sec`、`max_total_bytes`、`max_files`、`schedule`、`license_default`、`attribution`、`scope`（非空 dict）、`needs_key`（只写环境变量名）、`retention_note`。
2. `load_registry(dir)`：id 与文件名一致、不重复；报错指出文件和字段。
3. 检测明文 Key：字段名含 key/token/secret 且值不是 `env:` 开头时报错。
4. 许可字段：`license`（名称和 URL）、`attribution`（署名文本）、`usage_scope`（默认 `internal_only`）、`redistribution`（默认 `unverified`）、`license_verified_at`、`license_notes`。`redistribution` 不是 `allowed` 时 `usage_scope` 必须是 `internal_only`；不能假定所有日本来源都适用“公共データ利用規約”（例如国土地理院可能涉及測量法），逐个来源核对后才能放开。
5. 写 `sources/jp-egov-law.yaml`（许可先填 `unverified` / `internal_only`）和 `sources/README.md`（字段说明和示例，含许可字段的核对方法）。
6. 测试：合法加载、id 不符、重复、缺字段、明文 Key、scope 为空、未核对许可却标为可再分发。
7. （契约二对齐）`SourceSpec` 已在 `datumoj.core.contracts` 定义（许可五项合成 `license: LicenseInfo`，`needs_key` 只接受 `env:NAME`），本任务**不要另定义一份**：`registry.py` 负责读 yaml、校验文件名与 id、检测明文 Key，然后构造 `SourceSpec`，并实现 `Registry` 协议（以 `RegistrySuite` 为准）。

# 不做
- 不写其他来源的 yaml。
