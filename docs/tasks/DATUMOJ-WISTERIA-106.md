---
id: DATUMOJ-WISTERIA-106
title: 状态存储可配置：加本地目录后端，S3 / 目录按配置选，一处工厂取代四处写死的 S3
project: DATUMOJ
status: done
priority: 2
depends_on: ["DATUMOJ-WISTERIA-105"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/core/storage.py", "src/datumoj/rikolto/core/state_fs.py", "src/datumoj/rikolto/wiring.py", "src/datumoj/rikolto/core/pull.py", "src/datumoj/rikolto/core/schedule.py", "src/datumoj/rikolto/cloud.py", "src/datumoj/rikolto/cli.py", ".env.example", "docs/deploy/runners.md", "tests/core/test_state_fs.py", "tests/core/test_storage.py", "tests/regressions/test_audit_wisteria_106.py", "src/datumoj/rikolto/core/state_s3.py", "tests/regressions/test_audit_119.py", "results/DATUMOJ-WISTERIA-106/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/adr/**", "sources/**", "schemas/**", "eval/**", ".github/**", "src/datumoj/rikolto/core/contracts.py", "src/datumoj/rikolto/core/models.py", "src/datumoj/rikolto/core/state.py", "src/datumoj/rikolto/core/lease.py", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "tests/regressions/test_audit_[2-9]*.py", "tests/regressions/test_audit_10*.py", "tests/regressions/test_audit_11[0-8].py", "tests/regressions/test_audit_wisteria_0*.py", "tests/regressions/test_audit_wisteria_10[0-5].py", "tests/regressions/test_audit_wisteria_[2-9]*.py"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_106.py
    - cmd: make check T=tests/core/test_state_fs.py
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-106/summary.md 完成说明（含配置项表、目录后端的原子性做法和限制、每个改用工厂的调用点）"]
  review: {by: other_family, checklist: ["不设新配置时行为与现在完全一样（仍是 S3）", "目录后端是否通过现有 StateObjectStoreSuite 契约套件和 probe_conditional_writes", "条件写入是否真的原子（并发两个进程抢同一个 key 只有一个成功），不是先读再写", "ETag 是否每次写入都变，同字节重写也变", "四处写死的 S3StateObjectStore 是否都改走同一个工厂", "目录后端拒绝网络挂载的说明和检查是否到位", "文档和 .env.example 是否只有虚构值", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
2026-10-30 人定：运行环境先用 GitHub Actions + R2，但以后可能改成由某台机器抓取存到本地，或存到网上别的地方，这部分要做成可配置的。

现在"在哪跑"已经不绑死：任何机器都能跑 `rikolto cloud run`，`cloud-run.yml` 只是一层调用；写入方标记用 `DATUMOJ_WRITER_KIND`（actions / local / nas）。"存到网上别的地方"只要是 S3 兼容服务（R2、MinIO、AWS S3 等），改 `DATUMOJ_S3_*` 就行。

缺的是"存到本机目录"：状态存储只有 S3 一种实现，而且 `S3StateObjectStore` 在四处写死。本任务加一个本地目录后端，并让四处都按配置选后端。

# 背景
- 状态存储接口是 `core/contracts.py` 的 `StateObjectStore`（get / put / put_if_absent / put_if_match / delete_if_match / exists / list），租约和代次发布依赖它的条件写入是原子的。
- 写死 S3 的四处：`wiring.build_state`、`core/pull.py` 的 `pull()`、`core/schedule.py` 的 `_remote_lease`，以及经 `build_state` 的 `cloud.check` / `cloud.run`。
- `testing/contract_suites.py` 已有 `StateObjectStoreSuite`，`core/state_s3.py` 已有 `probe_conditional_writes`。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 契约、`state.py`、`lease.py` 不改；`state_s3.py` 只按"重做要求（第 1 轮）"改探测函数；需要改时写进 `summary.md` 并停下来问。
- 已有回归测试不许改，必须原样通过；唯一例外见"重做要求（第 1 轮）"第 2 条。
- 测试全部离线，临时文件放 `tmp_path`；仓库是公开的，不写账号、密钥、本机绝对路径。

# 要求
1. **配置**：`StorageConfig` 加 `state_backend: Literal["s3", "dir"]`，默认 `s3`（不设就和现在一样）；`dir` 时读 `DATUMOJ_STATE_DIR`（必须是绝对路径，不能和 `data_dir` 重叠）。环境变量名 `DATUMOJ_STATE_BACKEND`、`DATUMOJ_STATE_DIR`。
2. **目录后端** `core/state_fs.py` 的 `DirStateObjectStore`，实现 `StateObjectStore`：
   - key 映射成目录下的相对路径，拒绝 `..`、绝对路径和符号链接逃出根目录；
   - `put_if_absent`、`put_if_match`、`delete_if_match` 的检查和写入是同一个原子操作（例如按 key 的文件锁加临时文件、`os.replace`），不能先读再无条件写；
   - ETag 每次写入都变（同字节重写也变），调用方只能原样传回；
   - 条件不满足抛 `PreconditionFailed`，其他 I/O 错误不伪装成条件失败；
   - 只支持本机磁盘：文档写明网络挂载（SMB、NFS 等）上的文件锁不可靠，要用网络存储就用 S3 兼容服务。
3. **一处工厂**：在 `storage.py` 或 `wiring.py` 加 `build_state_store(config)`，四处调用都改用它。
4. **文档**：新建 `docs/deploy/runners.md`，用一张表写清几种组合怎么配：Actions + R2（现在）、某台机器 + 本地目录、某台机器 + S3 兼容服务；每种列出要设的环境变量、写入方标记、定时由谁触发（Actions 定时 / launchd / cron）。`.env.example` 补上新变量，只放虚构值。
5. **测试**：
   - `tests/core/test_state_fs.py`：目录后端跑 `StateObjectStoreSuite` 和 `probe_conditional_writes`；两个进程并发 `put_if_absent` 同一个 key 只有一个成功；路径逃逸被拒绝。
   - `test_audit_wisteria_106.py`：不设新变量时工厂仍给出 S3 后端；设成 `dir` 时，用目录后端完整走一次 `cloud.run`（离线假抓取）发布第一个代次，再用 `pull()` 拉到另一个目录并通过校验。
6. `make lint` 0 条，`make check T=tests` 全部通过。

# 不做
- 不接 S3 以外的网上服务（WebDAV、网盘等）；要用的话以后另加一个后端。
- 不改 `cloud-run.yml` 和定时，不改写入方标记的取值范围。
- shortcut：`cloud run` 每次仍从状态存储恢复到空目录；常驻机器上复用上次的工作目录以后另排。

# 重做要求（第 1 轮，2026-10-30）
上两次尝试暴露了任务文件的两处冲突，本轮从 `feature/0.4` 重新开始，按下面改：

1. **探测函数**：`state_s3.py` 的 `probe_conditional_writes()` 清理探测对象时直接用了 S3 私有客户端（`store._client.delete_object`），符合契约的目录后端因此探测失败。本轮只允许改这一处：
   - store 是 `S3StateObjectStore` 时，清理行为和现在完全一样；
   - 其他实现用公开接口清理（`get` 取到 ETag 后 `delete_if_match`）；
   - 清理失败仍然 `supported=False`、拒绝运行。
   - **不许**在目录后端里仿造 `_client`、`_bucket` 这类 S3 私有属性去迎合探测（第 2 次尝试的 `_ProbeCleanup` 写法不接受）。
   - `state_s3.py` 的其他部分一行不改。
2. **改名回归的环境变量清单**：`tests/regressions/test_audit_119.py` 的 `ENVIRONMENT` 冻结了代码里出现的全部 `DATUMOJ_*` 名字，新增变量必然让它失败。本轮只允许在这个元组里按字母序加入 `'DATUMOJ_STATE_BACKEND'` 和 `'DATUMOJ_STATE_DIR'` 两项，文件其他内容一字不改；不许再引入别的新 `DATUMOJ_*` 名字。
3. 其余要求不变，`make check T=tests` 必须全部通过。`summary.md` 里写明以上两处改动。
