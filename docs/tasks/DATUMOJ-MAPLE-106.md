---
id: DATUMOJ-MAPLE-106
title: 本机拉取：datumoj pull 增量下载最新有效代次、校验后切换；本机副本只读；MCP 报数据截止时间
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-MAPLE-102", "DATUMOJ-MAPLE-104"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/pull.py", "src/datumoj/cli.py", "src/datumoj/mcp_server.py", "src/datumoj/core/schedule.py", "tests/core/test_pull.py", "tests/test_mcp_server.py", "results/DATUMOJ-MAPLE-106/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_pull.py
    - cmd: make check T=tests/test_mcp_server.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-106/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
人在本机运行 `datumoj pull` 就能拿到 R2 上最新的有效代次，MCP 和 `datumoj search` 照常在本机回答，回答能说明数据截止到哪个代次的生成时间。

# 背景
开发线：A 云端运行与运行基础。计划 6.4 节。102 已有恢复到新目录并校验（`core/state.py`），104 已有租约。本机拉下来的是只读工作副本：本机不应在它上面抓取后又推回去覆盖 Actions 的状态。本机需要 R2 的**只读**凭证（与 Actions 的写凭证分开，由人准备），环境变量名不变。

计划见 `docs/plans/0.3-maple.md`（第 6.1–6.5 节是云端运行的设计，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。检查点 m0（证据正确性）已完成。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。存储边界见 `docs/adr/0002-network-storage.md`，证据身份见 `docs/adr/0004-evidence-identity.md`，状态代次见 `docs/adr/0005-state-generations.md`（由 MAPLE-001 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 / R2 一律用 moto（锁定的 moto 5.2.4 支持 `IfNoneMatch` / `IfMatch` 条件写入）。不连真实 R2。
- R2 凭证只从环境变量 `R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY` 读，endpoint、bucket 沿用 `core/storage.py` 的 `StorageConfig`（`R2_ENDPOINT`、`DATUMOJ_S3_BUCKET`、`DATUMOJ_S3_PREFIX`）。**仓库和 Actions 日志都是公开的**：命令输出、日志、异常信息里不出现凭证、endpoint、bucket 名和原始数据内容，只写计数、来源 ID、代次号和对象键的相对部分。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-106/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. `datumoj pull [--data-dir DIR] [--generation N]`：读当前有效代次；只下载本机还没有的对象（按 SHA256 判断，本机保留一份对象缓存或与本机上一次拉取的代次清单比对），raw 也只补本机没有的；用 102 的恢复在数据目录旁的新目录里组装并校验，通过后再原子地切换（旧目录保留一份作为上一版，再上一版的删掉）；校验不过不切换，原目录不变。
2. 本机副本记下所拉代次（代次号、生成时间、向量覆盖）。在拉下来的副本上运行 `schedule tick` / `run-now` / `state push` 时拒绝并说明原因（要在本机写，需要先取得租约并明确用 `--force-local` 之类的显式参数，本任务只做拒绝和提示，不做本机写回）。
3. MCP：`version` 工具的输出加上当前数据的代次号和生成时间（没有拉取记录时说明是本机自建数据）；向量覆盖不全时注明“向量覆盖 x / y”。`datumoj search` 的 JSON 输出同样带上。
4. 测试（moto）：首次拉取、无变化时第二次拉取不下载任何对象、只有一个来源变化时只下载变化的对象；拉取中途失败或校验失败时原目录不变；在拉下来的副本上 `schedule tick` 被拒绝；MCP `version` 带代次信息（`tests/test_mcp_server.py` 新增用例，已有用例不改）。
5. 测试断言：命令输出和日志里不出现测试用的凭证值、endpoint 和 bucket 名。
6. 全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不在本机做写回 R2。
- 不删除 R2 上的对象。
- 不改 MCP 其他工具的参数和输出。
