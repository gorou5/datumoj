---
id: DATUMOJ-MAPLE-109
title: raw 校验读取（D-09）：读出的字节按哈希校验，坏 blob 隔离并可由重抓修复
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-MAPLE-002"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/rawstore.py", "src/datumoj/core/rawstore_s3.py", "src/datumoj/testing/contract_suites.py", "tests/core/test_rawstore.py", "tests/core/test_rawstore_verify.py", "tests/test_evidence_regressions.py", "results/DATUMOJ-MAPLE-109/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/fakes.py"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_rawstore_verify.py
    - cmd: make check T=tests/core/test_rawstore.py
    - cmd: make check T=tests/core/test_rawstore_s3.py
    - cmd: make check T=tests/test_evidence_regressions.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-109/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
本机 raw 文件被改坏后，解析不会再拿坏字节按旧哈希“成功”解析；坏文件被隔离，下次抓到同样内容时自动修复。本机和 S3 两种实现遵守同一条规则。

# 背景
开发线：A 云端运行与运行基础。审核 D-09：`FileRawStore.open` / `get`（`core/rawstore.py`）直接返回文件内容，`verify` 是单独的方法；`put` 遇到同 SHA 文件已存在时不检查也不修复。`S3RawStore`（`core/rawstore_s3.py`）取回后已校验并抛 `CorruptedBlob`。管线（`pipeline.py`）读 raw 抛异常时会把这个快照记为解析失败，不发布结果。

计划见 `docs/plans/0.3-maple.md`（第 6.0 节是本检查点 m0 的问题表，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。身份与状态的约定见 `docs/adr/0004-evidence-identity.md`（由 MAPLE-002 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 审核问题的回归用例在 `tests/test_evidence_regressions.py`（MAPLE-002 写出，修好之前标 `xfail(strict=True)`，原因里写着负责的任务号）。修好本任务负责的用例后**删掉它们的 xfail 标记**，不改断言；其他任务的用例不动。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-109/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. `FileRawStore.get` 和 `open` 读出时边读边算 sha256（`open` 返回的流读完时校验），不符抛 `CorruptedBlob`，并把坏文件移到数据目录下的隔离区（`raw/.quarantine/<sha>-<时间>`），原位置不再有这个文件。
2. `put` 遇到同 SHA 已存在：先校验已有文件，坏的就隔离并用新字节写入（原子替换）；好的就照旧去重。`S3RawStore.put` 同样：已存在的对象校验不符时覆盖（S3 的 HEAD 不够时用 GET 校验，只在对象已存在时才做）。
3. 本任务允许在 `testing/contract_suites.py` 的 `RawStoreSuite` 里加用例（不改已有用例）：坏 blob 读出抛 `CorruptedBlob`；同内容再次 `put` 后可正常读出。本机和 S3 实现都通过。
4. 新测试 `tests/core/test_rawstore_verify.py` 覆盖：改坏文件后 `get` / `open` 抛错并隔离、重新 `put` 修复、管线遇到坏 blob 时该快照解析失败且不写 ChunkStore（用真实 `FileRawStore` + 管线）。删掉 `tests/test_evidence_regressions.py` 里本任务负责的用例（xfail 原因里写着本任务号）的 xfail 标记，它们必须通过。全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不改 RawStore 协议的方法签名。
- 不做定期全库校验任务。
