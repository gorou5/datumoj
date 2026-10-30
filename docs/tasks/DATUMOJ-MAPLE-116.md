---
id: DATUMOJ-MAPLE-116
title: NAS 运行包：镜像、compose 示例、条件写入探测和部署说明
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-115"]
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["deploy/nas/**", ".dockerignore", "docs/deploy/nas.md", "src/datumoj/core/state_s3.py", "src/datumoj/cloud.py", "src/datumoj/cli.py", "tests/core/test_state_s3.py", "tests/test_cloud_run.py", "tests/test_cloud_reports.py", "tests/integration/test_maple_m1.py", "tests/test_deploy_nas.py", "tests/regressions/test_audit_116.py", "results/DATUMOJ-MAPLE-116/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example", "docs/PLAN.md", "docs/plans/**", "docs/adr/**", "src/datumoj/core/storage.py"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_116.py
    - cmd: make check T=tests/core/test_state_s3.py
    - cmd: make check T=tests/test_cloud_run.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-116/summary.md 完成说明"]
  review: {by: human, checklist: ["探测是否覆盖三种条件写入，且用错误的条件确认会被拒绝", "不支持时是否拒绝运行、没有退路", "探测用的临时键是否清理、不碰正式键", "镜像和 compose 是否不含任何凭证、真实主机或内网地址", ".dockerignore 是否排除 .env、data、.orch 等本地内容", "部署说明的接管步骤是否保证抓取不断档", "全部已有测试是否通过", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
有一个可以直接放到 NAS 上的运行包：用镜像和 compose 每小时运行一次 `datumoj cloud run`，状态写入 NAS 上的 S3 兼容服务。运行前确认这个服务真的支持条件写入，不支持就拒绝运行。部署说明写清楚怎么装、怎么从本机接管。

# 背景
开发线：A 云端运行与运行基础。2026-10-25 决定：0.3 的写入端改为用户自己的 NAS（设备几天后到）。NAS 跑完整流程，状态放在 NAS 上的 S3 兼容服务（如 MinIO）里。MAPLE-115 已把 S3 配置改为通用名（`DATUMOJ_S3_*`）。

状态代次和写入租约的正确性依赖条件写入（`core/state_s3.py`）：
- `put_if_absent`：PUT 带 `If-None-Match: *`；
- `put_if_match`：PUT 带 `If-Match: <etag>`；
- `delete_if_match`：DELETE 带 `If-Match: <etag>`。
有些 S3 兼容服务或旧版本会忽略这些条件头、照常写入。那样两个写入者会互相覆盖，而且不报错。所以要在运行前实际试一遍，而不是相信服务的版本说明。

现在：
- `datumoj cloud run`（`cloud.py` 的 `run`）每次用空的工作目录，拿租约后运行到期来源、发布代次；`--max-embed 0` 时不做嵌入，向量留到以后。
- 嵌入用 Ollama，`index/embed.py` 的默认地址是 `http://localhost:11434`。
- 本机的 launchd 抓取在 NAS 接管前一直运行。JMA 只保留约 7 天，接管时不能断档。

计划见 `docs/plans/0.3-maple.md`（第 6.3 节），总计划 `docs/PLAN.md`。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。本任务不改契约；确实需要时写进 `results/DATUMOJ-MAPLE-116/summary.md` 并停下来问。
- 回归测试写在 `tests/regressions/test_audit_116.py`，断言修好以后的正确行为；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`，不写仓库里受 git 跟踪的路径。测试不需要 Docker，也不启动容器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。
- `tests/fixtures/` 下已有的样本只读。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **条件写入探测**：在 `core/state_s3.py` 加一个探测函数，在一个专用的临时键（如 `probe/<随机 id>`，不和任何正式键重名）上依次确认：
   - 键不存在时 `put_if_absent` 成功；键已存在时再 `put_if_absent` 被拒绝；
   - 用正确的 etag `put_if_match` 成功；用错误的 etag 被拒绝；
   - 用错误的 etag `delete_if_match` 被拒绝；用正确的 etag 删除成功。
   无论结果如何，最后都尽力删掉临时键。任何一步结果不符就返回“不支持”，并说明是哪一项；不回显 endpoint 和凭证。
2. **运行前检查**：`cloud run` 在拿租约之前先探测；不支持时以失败退出（`reason` 为固定的 `storage_conditional_unsupported`，`phase` 为 `storage_probe`），不写任何状态，也没有降级或跳过的选项。另加 `datumoj cloud check` 命令，只做探测并输出结果，供部署时先验证。
3. **镜像**（`deploy/nas/Dockerfile`）：
   - 用 `uv sync --frozen` 按 `uv.lock` 安装，不另装依赖；Python 版本与 Actions 工作流一致；
   - 镜像里不含 `.env`、凭证、数据目录；用非 root 用户运行；
   - 入口是 `datumoj`，默认命令 `cloud run`。
   - 仓库根目录加 `.dockerignore`：至少排除 `.env*`（`.env.example` 除外）、`data/`、`.orch/`、`results/`、`.git/`、`.venv/`、`handoff/`。
4. **compose 示例**（`deploy/nas/compose.example.yaml`）和 **环境变量示例**（`deploy/nas/env.example`，只有虚构值）：
   - 凭证和配置只通过 env 文件传入（`env_file`），不写在 compose 里；
   - 不对外发布端口；
   - 可选的 Ollama 服务放在单独的 profile 里，运行容器与它共享网络（如 `network_mode: "service:ollama"`），这样不用改 `embed.py` 的默认地址；
   - 不启用 Ollama 时，说明用 `--max-embed 0`，向量在 Mac 上补。
5. **定时**：由 NAS 自带的计划任务每小时（第 17 分钟）执行一次 `docker compose run --rm datumoj cloud run`；容器里不放常驻循环。重叠运行由已有的写入租约挡住（第二个返回 `skipped`）。
6. **部署说明** `docs/deploy/nas.md`：
   - MinIO（或其他 S3 兼容服务）的安装要点；为本项目建专用 bucket 和专用的访问密钥，只给这个 bucket 的权限；
   - 只在局域网或 VPN 内访问，不开公网端口；
   - 先跑 `datumoj cloud check`，通过后再加计划任务；
   - 首个代次：本机数据迁移成第一个代次（H3）的入口和顺序；
   - **接管步骤**：NAS 第一次成功运行某个来源后，再停掉本机 launchd 里的这个来源；按来源逐个接管；停掉前确认 NAS 上该来源最近一次成功时间晚于本机最近一次抓取；JMA 优先接管，接管前后都不能超过它约 7 天的保留期；
   - 回退：NAS 出问题时怎么恢复本机 launchd；
   - 向量两种做法（NAS 上跑 Ollama + bge-m3，或在 Mac 上补）；
   - 文档里只用 `nas.example`、`your-bucket-name` 之类的虚构值。
7. **测试**：
   - 探测：支持条件写入的存储（moto 或按 S3 语义实现的假客户端）判为支持；忽略条件头、照常写入的假客户端判为不支持，并指出是哪一项；三种操作各有一个“只这一项不支持”的用例；临时键在成功和失败后都被删掉；
   - `cloud run`：探测失败时不拿租约、不写状态、退出码非 0、`reason` 正确；
   - `tests/test_deploy_nas.py`：静态检查 Dockerfile、compose、env 示例和 `.dockerignore`：没有看起来像真实凭证或主机的值，compose 没有 `ports`，`.dockerignore` 排除了要求 3 列出的路径。
8. `make check T=tests` 全部通过；与新规则冲突的已有断言允许调整，在 summary 里逐条说明原因，不删测试。
9. **已有的 `cloud run` 测试要跟着改**（第 1、2 次尝试都因此失败）：`tests/test_cloud_reports.py`（以及 `tests/integration/test_maple_m1.py`，如受影响）用假存储或打桩的依赖调用 `cloud.run`，加了运行前探测后，这些假存储不支持条件写入，探测判为不支持，`test_cloud_run_only_claims_weekly_key_after_a_successful_round[False]` 因此得到 `failed`。这两个文件已加入 scope：让测试里的假存储按 S3 语义支持条件写入，或在这些测试里把探测打桩为“支持”；不得为了过测试给 `cloud run` 加跳过探测的开关或环境变量，也不改这些测试原本要断言的行为。

# 不做
- 不改 S3 配置的读取和变量名（MAPLE-115 已做）。
- 不改监控、写入者身份和 `datumoj pull` 的输出（MAPLE-117）。
- 不改嵌入端点的代码。
- 不做真实 NAS 上的运行（集成任务 904 在设备到货后做）。
- 不删 Actions 工作流和 R2 相关代码。
