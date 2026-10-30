---
id: DATUMOJ-MAPLE-120
title: 正式运行拒绝 http:// 的 S3 endpoint：只有本机回环地址允许明文 HTTP
project: DATUMOJ
status: ready
priority: 2
depends_on: []
runner: codex
model: null
tier: low
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/core/storage.py", ".env.example", "docs/deploy/runners.md", "tests/core/test_storage.py", "tests/core/test_storage_https.py", "results/DATUMOJ-MAPLE-120/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/adr/**", "schemas/**", ".github/**", "src/datumoj/rikolto/core/contracts.py", "src/datumoj/rikolto/core/models.py", "src/datumoj/rikolto/core/state.py", "src/datumoj/rikolto/core/lease.py", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "tests/regressions/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_storage_https.py
    - cmd: make check T=tests/core
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-120/summary.md 完成说明"]
  review: {by: other_family, checklist: ["回环判断是否按解析后的主机，不能被 localhost.example.com 之类绕过", "有没有留下关闭检查的开关", "现有用回环 http 的测试是否原样通过", "是否越界"]}
  human: none
max_attempts: 2
---
# 目标
状态存储的 S3 endpoint 只接受 HTTPS；明文 HTTP 只留给本机测试（MinIO、moto、`pytest-httpserver`）。

# 背景
计划 `docs/plans/0.3-maple.md` 6.2 节：“R2 endpoint 用 HTTPS 并校验证书；直接 HTTP 只用于本地 MinIO 测试。代码现在仍接受 `http://`，正式运行时拒绝 `http://` endpoint 另排任务（2026-10-27 已记，未做）。”本任务就是这一项。
- 校验在 `core/storage.py` 的 `StorageConfig`：现在只要求“无凭证、无查询参数的 HTTP(S) URL”。
- 现有测试里的 `http://` endpoint 都指向 `127.0.0.1` / `localhost`（例如 `tests/integration/test_tachibana_m2.py`、`tests/index/test_vector_object_storage.py`），本任务后必须原样通过。
- `.env.example` 第 20 行有 `http://nas.example:9000` 的示例，要改成 https。

本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。不新增依赖；不改契约；不新增环境变量（`tests/regressions/test_audit_119.py` 冻结了全部 `DATUMOJ_*` 名字）。

# 要求
1. `StorageConfig` 校验 endpoint：`https://` 一律接受；`http://` 只在主机是回环地址时接受（`localhost`、`127.0.0.0/8`、`::1`；按解析后的主机名判断，不按字符串前缀，`localhost.example.com`、`127.0.0.1.example` 这类要拒绝）；其他一律拒绝，错误信息说明“S3 endpoint 必须用 HTTPS（只有本机回环地址允许 http）”，不带出完整 URL 以外的配置值。
2. 不提供关闭这条检查的开关或环境变量。
3. 确认代码里没有关闭证书校验的地方（boto3 的 `verify=False` 等）；有就去掉，没有就在 summary 里写明查过。
4. `.env.example` 的示例改成 `https://`；`docs/deploy/runners.md` 的配置表加一句“endpoint 必须 HTTPS”。
5. 新测试 `tests/core/test_storage_https.py`：https 通过；`http://localhost`、`http://127.0.0.1:9000`、`http://[::1]:9000` 通过；`http://nas.example:9000`、`http://192.168.1.10:9000`、`http://localhost.example.com`、`http://127.0.0.1.example` 拒绝；`R2_ENDPOINT` 旧名同样受检查。
6. `make lint` 0 条，`make check T=tests` 全部通过。

# 不做
- 不改租约、代次、探测函数。
- 不加证书固定（pinning）。
