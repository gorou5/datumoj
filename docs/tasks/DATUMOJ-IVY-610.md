---
id: DATUMOJ-IVY-610
title: 抓取管理页面：datumoj admin（仅本机），总开关、各来源开关、状态与立即运行
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-IVY-107"]
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/admin/**", "src/datumoj/cli.py", "tests/test_admin.py", "results/DATUMOJ-IVY-610/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "sources/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/test_cli.py", "tests/test_cli_schedule.py", "tests/core/test_schedule.py"]
acceptance:
  automated:
    - cmd: make check T=tests/test_admin.py
    - cmd: make check T=tests/test_cli_schedule.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-610/summary.md 完成说明", "页面截图或 HTML 快照（results/DATUMOJ-IVY-610/ 下）"]
  review: {by: human, checklist: ["是否只监听 127.0.0.1", "改变状态的操作是否都要 POST 且带防跨站请求令牌", "页面上的开关是否和 control.json 一致（同一套 107 的接口）", "立即运行是否在后台执行、不阻塞页面，并受开关和锁约束", "来源文本是否经过转义", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
在本机浏览器里打开一个管理页面，就能看到每个抓取来源的状态，并开关总开关、每个来源的开关，或立即运行一次抓取。

# 背景
开发线：F 入口线。人决定（2026-10-21）：需要一个管理页面，所有抓取通道都要有开关，测试完本地开发机器可以关闭抓取。开关、调度、锁和运行记录由 DATUMOJ-IVY-107 提供（`src/datumoj/core/schedule.py`）；本任务只做页面，**不另外实现一套开关逻辑**。starlette 和 uvicorn 已在 `uv.lock` 里（随 mcp 安装），不需要新增依赖。计划见 `docs/PLAN.md`；测试全部离线。

# 要求
1. `datumoj admin [--port 8765] [--data-dir …]`：启动本机网页服务，**只监听 127.0.0.1**（不提供改成 0.0.0.0 的选项），启动时打印网址。
2. 页面（一个 HTML 页面即可，服务端渲染，不依赖外部 CDN）显示：
   - 总开关及其状态；
   - 每个来源一行：来源 ID 和名称、开关、调度频率、上次运行（开始时间、结果：新增/未变/失败及原因、停止原因）、下次到期、是否正在运行、快照数、该来源 raw 文件占用的大约磁盘空间；
   - 每行一个“立即运行”按钮。
3. 所有改变状态的操作（开关、立即运行）都是 **POST**，带服务端签发的防跨站请求令牌（每次启动随机生成，嵌在页面表单里）；GET 只读。令牌不对或来源不存在时返回错误，不改状态。
4. “立即运行”在后台线程或进程里调用 107 的 `run_now`，立即返回页面并显示“已开始”；开关关闭或已在运行时显示原因，不运行。
5. 页面上显示的所有来源文本（名称等）都要 HTML 转义。
6. 原有 `tests/test_cli.py`、`tests/test_cli_schedule.py`、`tests/core/test_schedule.py` 已列为 protected，必须全部通过。
7. 新测试 `tests/test_admin.py`（starlette 的 `TestClient`，假的抓取函数，不联网）：页面列出所有来源和状态；切换总开关和来源开关后 `control.json` 随之变化；没有令牌或令牌错误的 POST 被拒绝；GET 不改状态；立即运行在开关关闭时被拒绝、开启时触发一次（不阻塞）；来源名称里的 `<script>` 被转义；服务只绑定 127.0.0.1（检查启动参数）。
8. 在 `results/DATUMOJ-IVY-610/` 放一份页面的 HTML 快照，供人审查。

# 不做
- 不做登录和多用户，不对外网暴露，不改 107 的开关逻辑、来源配置、契约。
