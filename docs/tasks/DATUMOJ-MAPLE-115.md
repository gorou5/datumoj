---
id: DATUMOJ-MAPLE-115
title: 状态存储配置通用化：S3 配置用通用名，R2 与 Actions 保留不启用
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-114"]
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/storage.py", "src/datumoj/core/state_s3.py", "src/datumoj/core/rawstore_s3.py", "src/datumoj/core/lease.py", ".env.example", ".github/workflows/cloud-run.yml", "docs/adr/0002-network-storage.md", "tests/core/test_storage.py", "tests/core/test_state_s3.py", "tests/core/test_rawstore_s3.py", "tests/core/test_lease.py", "tests/test_cloud_workflow.py", "tests/regressions/test_audit_115.py", "results/DATUMOJ-MAPLE-115/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/PLAN.md", "docs/plans/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_115.py
    - cmd: make check T=tests/core/test_storage.py
    - cmd: make check T=tests/test_cloud_workflow.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-115/summary.md 完成说明"]
  review: {by: other_family, checklist: ["通用名是否在所有读取处生效", "R2_* 旧名是否仍可用", "新旧名同时设置且不一致时是否拒绝", "错误信息是否只提变量名、不回显值", ".env.example 是否只有虚构值", "cloud-run.yml 是否只剩手动触发", "没有改契约", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
状态存储和 raw 存储的 S3 配置改用不绑定 Cloudflare 的通用变量名，使 NAS 上的 S3 兼容服务（如 MinIO）可以直接接上；原有的 `R2_*` 配置继续可用。GitHub Actions 的定时运行停用，只留手动触发。

# 背景
开发线：A 云端运行与运行基础。2026-10-25 决定：0.3 的写入端从 GitHub Actions + R2 改为用户自己的 NAS。NAS 跑完整流程（抓取、解析、索引、发布代次），状态存放在 NAS 上的 S3 兼容服务里。R2 的代码和 Actions 工作流保留，但不启用。

现在 S3 配置分散在几处，名字绑定 R2：
- `core/storage.py` 的 `load_storage_config`：endpoint 先读 `R2_ENDPOINT`，再读 `DATUMOJ_S3_ENDPOINT`；报错文字是“缺少 R2_ENDPOINT（或 DATUMOJ_S3_ENDPOINT）”。
- `core/state_s3.py`、`core/rawstore_s3.py`：凭证只读 `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`；`state_s3.py` 第 34 行的报错写着 `R2_ENDPOINT`。
- `core/lease.py` 第 30 行列出 `DATUMOJ_S3_BUCKET`、`R2_ENDPOINT`、`DATUMOJ_S3_ENDPOINT`。
- `.env.example` 只有 Cloudflare 和 R2 的示例。
- `.github/workflows/cloud-run.yml` 有 `schedule: cron '17 * * * *'`。定时触发只在默认分支上生效，0.3 合进 main 后会每小时跑一次；没有凭证时每次都失败。

计划见 `docs/plans/0.3-maple.md`（第 6.2–6.5 节），总计划 `docs/PLAN.md`。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。本任务不改契约；确实需要时写进 `results/DATUMOJ-MAPLE-115/summary.md` 并停下来问。
- 回归测试写在 `tests/regressions/test_audit_115.py`，断言修好以后的正确行为；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`，不写仓库里受 git 跟踪的路径。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。
- `tests/fixtures/` 下已有的样本只读。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **通用名**：endpoint、bucket、prefix、region、凭证统一读 `DATUMOJ_S3_ENDPOINT`、`DATUMOJ_S3_BUCKET`、`DATUMOJ_S3_PREFIX`、`DATUMOJ_S3_REGION`、`DATUMOJ_S3_ACCESS_KEY_ID`、`DATUMOJ_S3_SECRET_ACCESS_KEY`。读取集中在一处（`core/storage.py`），`state_s3.py`、`rawstore_s3.py`、`lease.py` 都从这里取，不再各自读 `os.environ`。
2. **旧名兼容**：`R2_ENDPOINT`、`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY` 继续可用，作为对应通用名的备选。只设一种时用它；两种都设且值相同时照常运行；两种都设但值不同时拒绝运行，报错只写冲突的变量名，不回显值。
3. **报错文字**：缺配置时报通用名，例如“缺少 DATUMOJ_S3_ENDPOINT（旧名 R2_ENDPOINT 也可）”。任何报错和日志都不回显凭证、endpoint 的值。
4. **`.env.example`**：改成以 NAS 上的 S3 兼容服务为主的示例，只用虚构值（例如 `http://nas.example:9000`、`your-bucket-name`、`your-key-id-here`），并注明“局域网或 VPN 内访问，不开公网端口”。R2 的写法保留为注释里的另一种示例；Cloudflare 部署用的两行保留。
5. **`cloud-run.yml`**：去掉 `schedule`，只留 `workflow_dispatch`；其余（并发组、仓库判断、Ollama、锁定依赖）不动。环境变量引用可以继续用 `R2_*` secrets 名（兼容），不新增 secrets。`tests/test_cloud_workflow.py` 中触发器的断言相应改为只有 `workflow_dispatch`，其他断言保持。
6. **ADR 0002**：在末尾加一节“补充（MAPLE-115，2026-10-25）”：网络存储改为 NAS 上的 S3 兼容服务；R2 和 Actions 工作流保留不启用；变量名改为通用名、`R2_*` 作为旧名可用；条件写入能力的检查放在 MAPLE-116。不改前面已有的段落。
7. **回归测试** `tests/regressions/test_audit_115.py`：
   - 只设通用名时，`StorageConfig`、状态存储、raw 存储、租约都能构造（用 moto 或假客户端，不联网）；
   - 只设 `R2_*` 时同样可用；
   - 新旧名值不同时拒绝，报错里没有变量的值；
   - 缺 endpoint 时报错提到 `DATUMOJ_S3_ENDPOINT`；
   - `cloud-run.yml` 没有 `schedule`。
8. `make check T=tests` 全部通过；与新规则冲突的已有断言允许调整，在 summary 里逐条说明原因，不删测试。

# 不做
- 不做 NAS 的镜像、compose 和部署文档，也不做条件写入探测（MAPLE-116）。
- 不改监控和写入者身份（MAPLE-117）。
- 不删 R2 相关代码和 Actions 工作流，不改 Actions 的 secrets 名。
- 不改 `docs/plans/`、`docs/PLAN.md`。
