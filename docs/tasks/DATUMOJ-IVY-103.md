---
id: DATUMOJ-IVY-103
title: 礼貌 HTTP 客户端：限速、条件请求、重试、上限、回放
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-IVY-002"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/http.py", "tests/core/test_http.py", "results/DATUMOJ-IVY-103/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: /opt/homebrew/bin/uv run --frozen pytest -q tests/core/test_http.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-103/summary.md 完成说明"]
  review: {by: other_family, checklist: ["回放未登记 URL 是否一定报错", "Key 是否在所有输出里都被去掉", "超限是否边下边判", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
所有采集器共用一个对政府站点礼貌的 HTTP 客户端，测试时可以无网络回放录制样本。

# 背景
开发线：A 抓取基础。参考旧项目 `/Users/aaron/codex_workspace/digital-assets/repo/plugin-prototype/runtime/src/adapters/http-readonly.ts` 的安全设计，再补上限速和重试。PLAN 第 5 节“抓取守则”。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。不要 import 其他开发线的实现模块（集成任务除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-103/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. 实现 `HttpFetcher` 协议，基于 httpx 同步客户端：`fetch(url, etag=None, last_modified=None) -> FetchResult`。
2. 按 host 限速（默认 1 请求/秒），时钟可注入，测试不真的 sleep。
3. 条件请求；304 → `not_modified`。429/5xx/网络错误指数退避重试，次数有上限，尊重 `Retry-After`；其他 4xx 不重试。
4. 超过 `max_bytes` 边下边判、立即中止 → `too_large`。
5. 重定向逐跳检查：只允许 http/https、host 在 `allowed_hosts`（支持 `*.go.jp`）里，拒绝私网和回环地址。
6. 请求 identity 编码但收到 gzip 时正确解压。
7. 所有 URL 输出用 `redact_url`。
8. `RobotsCache`：按 host 缓存 robots.txt，被禁止时 `blocked_by_robots`，不发请求。
9. `ReplayTransport(mapping)`：URL（忽略 Key 参数、参数顺序无关）映射到 fixture 文件和状态码；未登记的 URL 直接报错。
10. 测试用 pytest-httpserver 或 `httpx.MockTransport` 覆盖以上每条。

# 不做
- 不写 raw 存储和 manifest。
