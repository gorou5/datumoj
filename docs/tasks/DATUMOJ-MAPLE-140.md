---
id: DATUMOJ-MAPLE-140
title: 合并 main 前清掉审查遗留：去掉状态块 Actions 缓存，修嵌入、e-Stat、Federal Register 与测试隔离
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: [".github/workflows/cloud-run.yml", ".github/workflows/cloud-embed.yml", "src/datumoj/rikolto/index/embed.py", "src/datumoj/rikolto/crawlers/estat_api.py", "src/datumoj/rikolto/crawlers/fedreg.py", "sources/jp-estat-api.yaml", "sources/intl-wdi.yaml", "docs/deploy/runners.md", "docs/deploy/nas.md", "tests/**", "results/DATUMOJ-MAPLE-140/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".env*", "catalog/**", "src/datumoj/rikolto/cloud.py", "src/datumoj/rikolto/parsers/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/test_cloud_workflow.py
    - cmd: make check T=tests/regressions/test_audit_140.py
    - cmd: make lint
    - cmd: make check T="tests --deselect=tests/integration/test_wisteria_m1.py::test_us_dollar_series_ranks_first_for_gdp_usd"
  evidence: ["测试输出日志", "变更文件清单", "docs/deploy/runners.md 里更新后的缓存说明"]
  review: {by: other_family, checklist: ["两个云工作流是否不再把状态块写入或读出 Actions 缓存，嵌入模型缓存是否保留", "没有缓存时恢复是否仍走 R2 下载和校验", "连接超时是否不再拆批，读取超时拆批是否保留", "4xx 错误信息是否不含响应正文", "e-Stat 目录页 304 时是否继续翻页，且正文取自上次成功的快照", "两个来源 yaml 的显式值是否等于原来的默认值，没有放宽许可"]}
  human: sampled
max_attempts: 5
---
# 目标
清掉 `feature/0.4` 合并进 `main` 前的 CodeRabbit 遗留问题，特别是受限文本经 Actions 缓存外流的风险。

# 背景
- 仓库公开。`cloud-run.yml` 和 `cloud-embed.yml` 用 `actions/cache` 保存状态块（含 `chunks.sqlite`、`keyword.sqlite` 的内容，可能有 `no_outflow` 来源的正文）。fork 发来的 PR 可以恢复目标分支的缓存。
- 去掉缓存的代价已实测：恢复从约 180 秒变为约 310 秒，可以接受。恢复逻辑本来就把缓存当作可验证的内容来源，缺失时从 R2 下载。
- `index/embed.py` 对所有 `httpx.TimeoutException` 拆批。目标主机不可达时，连接超时也会拆批，一个 32 条的批次最多 159 次请求。
- `index/embed.py` 的 4xx 错误带响应正文前 200 字符。服务端回显输入时，私有文本会进入异常和日志。
- `crawlers/estat_api.py` 的 `after_fetch` 只处理 `FetchOutcome.ok`。目录页 304 时不再翻页，后面变化的页面刷新不到。`data_europa.py`、`udata.py`、`eurostat.py`、`fedreg.py` 都从 `ctx.manifest.last_success` 读上次成功的正文。
- `crawlers/fedreg.py` 的 `_allowed` 接受 `http`，详情 JSON 里的 XML 链接可能走明文。
- `sources/jp-estat-api.yaml`、`sources/intl-wdi.yaml` 没有显式写 `outflow`、`cloud_model_ok`，靠默认值。
- `tests/integration/test_estat_api_e2e.py`、`test_wdi_e2e.py` 没固定 raw 后端，继承 `DATUMOJ_RAW_BACKEND=s3` 时可能写到远端。
- `docs/deploy/nas.md` 写“推荐” HTTPS，而 `StorageConfig.validate_s3` 实际拒绝非回环的 `http://`。

任务分支从 `feature/0.4` 拉出，合并回它。测试全部离线，不新增依赖；不写账号、密钥、本机绝对路径。

# 要求
1. 两个工作流删除 `Restore state blocks` 和 `Save state blocks` 步骤。保留嵌入模型缓存、`Sync source health issues` 和其他步骤。不改预算参数。
2. 改写 `tests/test_cloud_workflow.py` 的 `test_state_cache_is_saved_even_when_a_source_fails_and_holds_only_blocks`：断言两个工作流都没有引用 `rikolto-blocks` 或 `rikolto-state-` 的 `actions/cache*` 步骤。
3. 更新 `docs/deploy/runners.md` 的状态块缓存一节：写明不再使用 Actions 缓存及原因，每轮从 R2 下载。不改 `core/`；如果代码里留有读取 `rikolto-blocks` 目录的逻辑，目录不存在时必须仍能运行（用测试确认），不要删。
4. `index/embed.py`：只有 `httpx.ReadTimeout` 拆批。其他 `TimeoutException`（含连接超时）走普通重试。
5. `index/embed.py`：非 200 且非 5xx 的错误只保留状态码，不带响应正文。
6. `crawlers/estat_api.py`：目录页 `not_modified` 时，按其他爬虫的做法从 `ctx.manifest.last_success` 取上次成功的正文继续翻页。取不到时不抛异常，返回 `()`。
7. `crawlers/fedreg.py`：`_allowed` 只接受 `https`。
8. `sources/jp-estat-api.yaml`、`sources/intl-wdi.yaml` 在 `license` 下显式写 `outflow: no_outflow`、`cloud_model_ok: false`，其他字段不动。
9. 两个 e2e 测试用 `monkeypatch` 在 `wiring.build` 之前固定本地 raw 后端（`DATUMOJ_RAW_BACKEND=local`，以 `wiring` 和配置实际接受的写法为准）。
10. `docs/deploy/nas.md`：非回环地址必须用 HTTPS（把“推荐”改为“必须”），回环地址可以用 HTTP。
11. 新增 `tests/regressions/test_audit_140.py`，覆盖：
    - 连接超时不拆批、按重试次数重试；读取超时仍拆批；
    - 4xx 响应回显输入时，异常信息不含输入文本；
    - e-Stat 目录第 1 页 304 时仍会产出下一页，取不到上次正文时返回空；
    - Federal Register 的 `http://` XML 链接被拒绝、`https://` 接受；
    - 两个 yaml 的 `outflow`、`cloud_model_ok` 显式存在且取值如上；
    - 状态块缓存目录不存在时恢复走 R2 下载。
    fixtures 不改，测试数据写在测试里。
12. `make lint` 0 条，全量测试全部通过，要跑完，不要中途中断。全量命令排除 `test_wisteria_m1.py` 里现存的 1 个 xfail（调度器把 xfail 算作失败，与本任务无关）。不要修改那个测试，也不要新增 xfail 或 skip。
13. 调度器里没有人能回答问题，不要等待回复。遇到范围缺口，在 scope 内选最小的做法完成，写进 `docs/deploy/runners.md`。

# 不做
- 不改 `cloud.py`。补嵌失败不写 `source_health` 另开 issue 跟进。
- 不改 `core/`，不改解析器和解析器版本。
- 不改 `sources/*.yaml` 里除上述两个字段外的内容。
- 不操作真实 R2 和 GitHub，不删除已有 Actions 缓存。
