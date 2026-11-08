---
id: DATUMOJ-MAPLE-121
title: 写入租约释放不再依赖条件删除：改为条件覆盖成"已释放"记录；存储探测去掉 DELETE If-Match（R2 不支持）
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/core/lease.py", "src/datumoj/rikolto/core/state_s3.py", "tests/core/test_lease.py", "tests/core/test_state_s3.py", "tests/test_cloud_run.py", "tests/regressions/test_audit_116.py", "tests/regressions/test_audit_121.py", "tests/regressions/test_audit_wisteria_106.py", "tests/integration/test_maple_m1.py", "docs/adr/0005-state-generations.md", "docs/deploy/nas.md", "results/DATUMOJ-MAPLE-121/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".github/**", ".env*", "sources/**", "catalog/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/models.py", "src/datumoj/rikolto/core/contracts.py", "src/datumoj/rikolto/core/state_fs.py", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_lease.py
    - cmd: make check T=tests/core/test_state_s3.py
    - cmd: make check T=tests/test_cloud_run.py
    - cmd: make check T=tests/regressions
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-121/summary.md 完成说明"]
  review: {by: other_family, checklist: ["释放、续期、接管是否全部只用 put_if_absent / put_if_match，生产路径不再调用 delete_if_match", "旧持有者在被接管后释放、续期是否仍然失败，不会覆盖继任者的租约", "已释放记录是否不含 token，status 是否报未持有", "释放后不加 --force 能否重新取得；未过期的有效租约是否仍不能被抢", "格式损坏的租约对象是否仍失败关闭", "探测是否仍检查 If-None-Match 和 If-Match 的正反两面，且只碰 probe/ 下的临时键", "是否改了契约、models、dir 后端或其他模块", "是否越界"]}
  human: sampled
max_attempts: 3
---
# 目标
`rikolto cloud run` 能在 Cloudflare R2 上跑：写入租约的释放不再依赖 R2 不支持的"按 ETag 条件删除"，启动时的存储探测也不再要求它。

# 背景
2026-11-01 用真实的 R2 凭据在本机跑了项目自带的 `probe_conditional_writes`：PUT `If-None-Match: *`、PUT `If-Match`（含错误 ETag 返回 412）都正常，但 **DELETE 带错误的 `If-Match` 时 R2 不报错、照样删除**，探测停在 `delete_if_match_wrong_etag`，Cloud run 因此以 `storage_conditional_unsupported` 拒绝运行。

生产代码里只有两处用到条件删除：`core/lease.py` 的 `release()`（ADR 0005 写的"以当前 ETag 执行 `delete_if_match`"），以及 `core/state_s3.py` 的探测。条件删除在 R2 上不可靠，所以释放租约要换成条件覆盖：用 `put_if_match` 把租约对象替换成一条"已释放"记录。R2 上 `put_if_match` 是可靠的，旧持有者也就不可能删掉或覆盖继任者的新租约。

本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 契约（`core/contracts.py`）、`core/models.py`、dir 后端（`core/state_fs.py`）和 `testing/` 不改。`StateObjectStore.delete_if_match` 继续留在契约和各实现里，只是生产路径不再调用。
- `WriteLease` 要求 `expires_at > acquired_at`，不能拿来表示"已释放"。已释放记录用 `lease.py` 里自己定义的一个小格式（例如 `{"released_at": "<带时区时间>"}`），不放进 `models.py`，不含 holder 和 token。
- 测试全部离线，S3 用现有测试的 moto / 假客户端。不新增依赖，不改 `pyproject.toml`、`uv.lock`。仓库是公开的，不写账号、密钥、本机绝对路径。

# 要求
1. **释放**：`release(holder, token)` 先核对 holder 和 token（照旧允许已过期的持有者释放），然后用当前 ETag 执行 `put_if_match`，把租约对象换成已释放记录。条件失败抛 `LeaseError("租约释放竞争失败")`，不改动继任者的对象。
2. **读取**：`_read()` 能识别已释放记录。其他无法解析的内容仍按"租约格式无效"失败关闭。
   - `status()` 遇到已释放记录时报 `{"held": False}`。
   - `_owned()`、`renew()`、`check()`、`credentials()` 遇到已释放记录时，一律视为"不属于本次持有者"。
3. **取得**：
   - 对象不存在时照旧用 `put_if_absent`。
   - 对象是已释放记录时，**不需要 `--force`**，用它的 ETag 执行 `put_if_match` 写入新租约。CAS 失败抛"租约竞争失败"，本轮作废。
   - 对象是有效租约时照旧拒绝。对象是已过期的租约时，照旧只能用 `--force` 接管（流程不变）。
4. **探测** `probe_conditional_writes`：去掉 `delete_if_match_wrong_etag` 和 `delete_if_match_delete` 两步。在 `put_if_match_wrong_etag` 之后读回、确认内容未变，就算 `complete`。清理照旧用无条件 DELETE，只删本次的 `probe/<uuid>` 键。返回结构不变（`supported`、`check`，以及清理失败时的 `cleanup_failed`）。
5. `S3StateObjectStore.delete_if_match` 的实现不改，加一行注释：部分 S3 兼容服务（如 R2）会忽略 DELETE 上的 `If-Match`，生产路径不能依赖它。
6. **测试**：
   - `tests/core/test_lease.py`：
     - 原来针对释放竞争的用例（monkeypatch `delete_if_match` 的）改为针对 `put_if_match`。
     - 新增：释放后不加 `--force` 能重新取得；旧持有者释放后不能续期，也不能再次释放；被接管后旧持有者释放失败，且继任者的租约字节不变；已释放记录不含 token。
   - **R2 行为模拟**：给 `tests/core/test_state_s3.py` 加一个替身，让 `delete_object` 忽略 `IfMatch`、总是删除。断言：
     - 探测结果为 `supported: True`；
     - 在这个替身上跑完整的取得 → 释放 → 再取得、取得 → 过期 → 强制接管 → 旧持有者释放，结果都正确。
   - `tests/regressions/test_audit_116.py` 第 35 行把"DELETE 条件不支持"当作探测失败；这个预期要改成"不影响探测"，其余参数不变。在 summary 里写明这是有意的改动和理由。
   - 新增 `tests/regressions/test_audit_121.py`：
     - 断言 `src/datumoj/rikolto/` 下除 `core/state_s3.py`、`core/state_fs.py`、`core/contracts.py`、`testing/` 外，没有代码调用 `delete_if_match`；
     - 断言在"忽略 DELETE If-Match"的替身上，旧持有者无法让继任者失去租约。
   - `tests/test_cloud_run.py` 里与探测步骤名有关的断言跟着改。
   - **其他断言"释放后租约对象不存在"的测试**（2026-11-02 第 2 次尝试失败的原因）：`tests/regressions/test_audit_wisteria_106.py:78`、`tests/integration/test_maple_m1.py:211` 和 `:316`、`tests/test_cloud_run.py:123` 都断言 `write-lease.json` 为 `None`。释放后对象现在是已释放记录，这些断言改为"是已释放记录、不含 token，且 `LeaseManager(store).status()` 为 `{"held": False}`"，测试的其余内容不改。改之前先 `rg 'write-lease.json") is None'` 确认没有漏掉的。
7. **文档**：
   - ADR 0005"写入租约"一节里的释放一条，改为条件覆盖成已释放记录，并注明原因：R2 不支持 DELETE `If-Match`，2026-11-01 实测。
   - `docs/deploy/nas.md` 第 12 行对存储服务的要求，去掉 DELETE `If-Match`。
   - 只改这两处，不改历史任务和计划文件。
8. `make lint` 0 条，`make check T=tests` 全部通过；全量测试要跑完，不要中途中断。

# 不做
- 不改契约、`models.py`、dir 后端；不删 `delete_if_match`。
- 不改 `cloud.py` 的流程和返回结构（只要探测通过，后面流程不变）。
- 不做真实联网测试，不碰任何真实凭据；真实 R2 上的验证由主对话在合并后重跑 Cloud run 完成。
