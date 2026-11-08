---
id: DATUMOJ-MAPLE-123
title: 状态发布与拉取提速：副本并行上传/下载、写入前的租约检查限频，并给发布各阶段计时
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/core/state.py", "src/datumoj/rikolto/core/state_s3.py", "src/datumoj/rikolto/cloud.py", "tests/core/test_state.py", "tests/core/test_state_s3.py", "tests/test_cloud_run.py", "tests/regressions/test_audit_122.py", "tests/regressions/test_audit_123.py", "docs/deploy/runners.md", "results/DATUMOJ-MAPLE-123/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".github/**", ".env*", "sources/**", "catalog/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/pipeline.py", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/contracts.py", "src/datumoj/rikolto/core/models.py", "src/datumoj/rikolto/core/state_fs.py", "src/datumoj/rikolto/core/lease.py", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_state.py
    - cmd: make check T=tests/core/test_state_s3.py
    - cmd: make check T=tests/test_cloud_run.py
    - cmd: make check T=tests/regressions
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-123/summary.md 完成说明"]
  review: {by: other_family, checklist: ["并行只用于内容寻址、不可变的 copies/ 与 raw/ 键，generations/N.json 与 current.json 仍是最后、串行、带条件写", "任何一个并行上传失败时是否不发布新代次（不写 generations/N.json、不切 current.json）", "限频是否只作用于 copies/、raw/ 上传；写 generations/N.json 前和发布前（before_publish）是否仍做完整的租约核对", "默认并发为 1 时行为与改前完全相同；dir 后端和契约是否没改", "阶段计时是否只有阶段名和秒数，没有 key、路径、异常文本", "是否越界"]}
  human: sampled
max_attempts: 5
---
# 目标
一次 Cloud run 的发布和开头的状态还原，耗时主要取决于数据量和带宽，而不是对象个数乘以往返延迟。发布的每一段花了多少时间，要能从结果 JSON 和日志里看出来。

# 背景
2026-11-01 第 2 轮 Cloud run（第 2 代）：
- 发布从 23:43:00 开始。23:43:06 到 23:58:16，**串行**上传了 1050 个 `copies/sha256/…` 对象，约 58 MB，平均每个约 0.86 秒。
- 每次上传前，`StateManager._check_write()` 都会调用 `before_write`。cloud 把它设成租约的 `check`，每次都从远端读一遍 `write-lease.json`，所以一个对象要 2 次往返。
- 23:58:17 写入 `generations/2.json`，23:58:19 写入 `current.json`。但 `push()` 一直到 00:16:16 才返回，**中间 18 分钟花在哪里不知道**。日志被 `_quiet()` 吞掉了，push 内部也没有计时。
- 本机 `rikolto pull` 第 2 代（2476 个文件，约 530 MB）也用了 10 分钟以上，同样是串行下载。

本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 契约（`core/contracts.py`）、`models.py`、dir 后端（`core/state_fs.py`）、`core/lease.py` 和 `testing/` 不改。契约没有承诺 `StateObjectStore` 线程安全，所以并发是 `StateManager` 的**可选参数，默认 1**，只有 cloud 在 S3 后端上打开。
- 测试全部离线，S3 用现有测试的 moto 或假客户端。不新增依赖（并发用标准库 `concurrent.futures`）。仓库是公开的，不写账号、密钥、本机绝对路径。

# 要求
1. **并行上传**：`StateManager` 新增关键字参数 `transfer_workers: int = 1`（校验：正整数）。
   - `_push()` 里只并行"内容寻址、不可变"的两类对象：`copies/sha256/…` 和 `raw/…`。用线程池；去重集合 `existing` 的更新要加锁，或在主线程汇总。
   - 任何一个上传抛错：等已提交的任务结束，然后照现有方式失败（`StateError("状态推送失败")`），**不写** `generations/N.json`，不切 `current.json`。
   - `generations/N.json` 和 `current.json` 的写法、顺序、条件都不变，仍在全部上传完成之后串行执行。
   - 续传孤儿候选（`_materialize(..., raw="remote")`）的路径行为不变。
2. **并行下载**：`_materialize` 下载副本（包括 SQLite 分块）时也用 `transfer_workers`。分块按编号顺序拼回、校验 SHA256 的逻辑不变。
3. **S3 连接池**：`core/state_s3.py` 的 botocore `Config` 加上 `max_pool_connections`，取值不小于 cloud 用的并发数。超时和重试设置不变。
4. **租约检查限频**（`cloud.py`，不改 `lease.py`）：给 `manager.before_write` 的不再是 `check` 本身，而是一个包装：距上次调用 `check` 不到 30 秒就直接返回，否则调用 `check`（它先看续期线程的 `lost`，再读远端）。用单调时钟，可注入以便测试。
   - `before_publish` 仍然直接用 `check`，每次做完整核对。
   - 理由写进 summary：限频只影响内容寻址、不可变的 `copies/`、`raw/` 上传，租约丢了多传几个也无害；`generations/N.json` 前的 `_check_write`、发布前核对和 `current.json` 的 CAS 保证不会发布。写明最坏发现延迟是 30 秒。
   - `generations/N.json` 写入前那一次 `_check_write` 要绕过限频，做完整核对（可以在调用前重置限频时间，或另给一个入口，实现自定）。
5. **cloud 使用并发**：S3 后端时，`StateManager` 用 `transfer_workers=8`。
6. **阶段计时**：
   - `StateManager` 在每次 `push()` 和 `pull`/还原之后，把各阶段耗时存在实例属性 `last_timings: dict[str, float]` 里，例如 `stage`、`validate`、`upload_copies`、`upload_raw`、`sample_provenance`、`write_generation`、`publish`、`cleanup`。阶段名由实现定，但要覆盖从进入 `_push` 到返回的全部时间，**包括退出临时目录的清理**。
   - cloud 把它放进结果 JSON 的新字段 `publish_timings`（以及还原的 `restore_timings`），也放进 `publish_end` 进度行。进度行的允许键相应加上这两个；值只能是阶段名和秒数。
   - 第 2 轮那 18 分钟要能落到某个阶段里。如果离线测试里能复现到某个阶段明显偏慢（比如清理、样本核对），在 summary 里写出来。
7. **测试**：
   - `tests/core/test_state.py`：`transfer_workers=4` 时推送和拉取的结果，与 `transfer_workers=1` 时逐字节相同；并发上传中某一个对象失败时，不出现 `generations/N.json`，`current.json` 不变；`transfer_workers` 为 0 或非整数时报错。
   - `tests/core/test_state_s3.py`：在 moto 或假客户端上，用并发跑完整的推送 → 拉取。
   - `tests/test_cloud_run.py`：限频包装在 30 秒内只调用一次 `check`，超过 30 秒再调用；写 `generations/N.json` 前和发布前一定调用；`publish_timings` 存在，并且只含字符串键和数值。
   - 新增 `tests/regressions/test_audit_123.py`：用计数的假存储，断言发布 N 个新副本时，远端租约读取次数远小于 N（例如 ≤ 2 + 耗时 / 30 秒）；进度行的键仍是白名单。
   - `tests/regressions/test_audit_122.py` 里进度行的允许键集合，只加上 `publish_timings` 和 `restore_timings`，其余断言不变。
   - 其他已有的测试和断言全部不变。
8. `docs/deploy/runners.md` 加一两句话：发布并发的含义，以及 `publish_timings` 怎么看。
9. `make lint` 0 条，`make check T=tests` 全部通过；全量测试要跑完，不要中途中断。

# 打回记录
- 第 1–3 次：全量验收都失败在同样两处。
  - `tests/regressions/test_audit_122.py` 的进度行键白名单没有加上新键。当时这个文件不在 scope 里，现在已加入，按要求 7 改。
  - `tests/test_cloud_workflow.py` 的作业时限断言，与本任务无关，已由主对话在 #80 修好并合并进 `feature/0.4`。从最新的 `feature/0.4` 重来，不要改这个文件。

# 不做
- 不改契约、`models.py`、dir 后端、`lease.py`、`pipeline.py`。
- 不改 `.github/workflows/`。
- 不改向量化（另有任务）。不做真实联网测试，不碰任何真实凭据。真实 R2 上的效果由主对话在合并后重跑 Cloud run 来验证。
