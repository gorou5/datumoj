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
    - cmd: make check T=tests/core/test_http.py
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
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块（例如 304 用 302 的地区码表）；其他任务的实现模块一律不 import（集成任务 9xx 除外）。
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
11. （审查多轮后明确，必须有测试锁定，防止回退）回放匹配按 URL **原始路径**比较，不做百分号解码：`/a%2Fb` 和 `/a/b` 是两个不同的地址，只登记一个时请求另一个必须抛 `UnregisteredUrl`。参数比较只按**参数名**识别 Key（`appId`、`api_key` 等），不能按参数值（例如值恰好是 `REDACTED` 的普通参数不能被删掉）。
12. （审查多轮后明确，必须有测试锁定）robots.txt 判定：匹配前先把路径里**非保留字符**的百分号编码解码（如 `%70` → `p`），保留字符（如 `%2F`）不解码；规则按 RFC 9309 取**最长匹配**，`Allow` 与 `Disallow` 等长时 `Allow` 优先，不能依赖 `urllib.robotparser` 的书写顺序语义；支持 `*` 和 `$`。**跟随重定向时，每一跳的目标都要重新做 robots 判定**（目标被禁止就返回 `blocked_by_robots`，不请求目标）。
13. （审查多轮后明确，必须有测试锁定）gzip 解码要处理多段（multi-member）流，上限按解压后的总字节数边解边判。日志、异常、结果里的 URL 一律去 Key，包括 httpx 自带的请求日志。
14. 提交前对照以上三条以及历次打回理由（任务正文末尾）逐条自查，每条都有对应测试。

# 不做
- 不写 raw 存储和 manifest。
