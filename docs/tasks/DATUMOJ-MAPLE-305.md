---
id: DATUMOJ-MAPLE-305
title: 医疗开放数据（NDB 第 11 回特定健診質問票）和 ABR 町字：ZIP 内取指定文件、遮蔽值原样保留、前导零不丢
project: DATUMOJ
status: ready
priority: 2
depends_on: ["DATUMOJ-MAPLE-304"]
runner: codex
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/crawlers/mhlw_ndb.py", "src/datumoj/rikolto/parsers/mhlw_ndb.py", "src/datumoj/rikolto/crawlers/abr.py", "src/datumoj/rikolto/parsers/abr.py", "sources/jp-mhlw.yaml", "sources/jp-abr.yaml", "tests/crawlers/test_mhlw_ndb.py", "tests/parsers/test_mhlw_ndb.py", "tests/crawlers/test_abr.py", "tests/parsers/test_abr.py", "tests/integration/test_jp_files2_e2e.py", "catalog/jp-mhlw.json", "catalog/jp-abr.json", "results/DATUMOJ-MAPLE-305/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/**", "schemas/**", ".github/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/wiring.py", "src/datumoj/eltiro/**", "sources/README.md"]
protected: ["tests/fixtures/**", "tests/regressions/**"]
acceptance:
  automated:
    - cmd: make check T=tests/parsers/test_mhlw_ndb.py
    - cmd: make check T=tests/parsers/test_abr.py
    - cmd: make check T=tests/integration/test_jp_files2_e2e.py
    - cmd: make check T=tests/parsers/test_zip_safe.py
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-305/summary.md 完成说明（两个来源分别写验收结果）"]
  review: {by: other_family, checklist: ["许可、署名、allowed_hosts、频率和上限是否和来源决策文档一致", "是否只请求白名单主机、遵守 robots，不绕过", "单位、时间、注记是否原样保留，没有补零或自行换算", "是否有联网的测试", "是否改了契约、wiring 或其他来源的实现", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
NDB 特定健診質問票的都道府県 × 性别 × 年龄分布进 `query_sql`；ABR 町字能按市町村检索。两个来源分别验收。

# 背景
来源决策文档 3.3（医疗开放数据、ABR 两节）、4.5、6.1–6.4 节；样本 `tests/fixtures/ndb/`（从 3MB ZIP 里取出的单个 xlsx，`derived`）和 `tests/fixtures/abr/mt_town_city134023.csv.zip`（原样，561 字节）。
- ZIP 安全解压用 304 的 `parsers/_zip.py`（只用，不改；要改写进 summary 并停下来问）。
- NDB 的小格子遮蔽（决策文档 3.3）：集计单位小于 10 显示半角 `-`；为防反推，10 以上的最小值也可能被遮；特定健診不适用“0 不遮”的例外。

计划见 `docs/plans/0.3-maple.md`（第 5、6.6 节）和来源决策 `docs/plans/0.3-maple-sources.md`（下文列出的节）。本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- **按插件接入**：`crawlers/` 加一个模块（`Crawler` 子类声明 `kind`），`parsers/` 加一个模块（`Parser` 子类声明 `source_kind`），再加 `sources/<id>.yaml`；`discover_crawlers` / `discover_parsers` 会自动发现，不改 `wiring.py`。写法参照已有的 `stat_dashboard`（API → 观测值）、`ckan`（目录 → Dataset）、`estat_files`（文件 → 观测值）。
- 契约（`core/contracts.py`、`core/models.py`）和 schema 不改；需要改时写进 `summary.md` 并停下来问。
- 样本在 `tests/fixtures/<目录>/`（H1，PR #32 已合并），只读；每个目录的 README 写了 URL、过滤参数、样本类型和许可。测试全部离线，HTTP 只用这些样本或 `pytest-httpserver`。
- yaml 的值按决策文档：`schedule` 见 6.1，`rate_per_sec` / `max_total_bytes` / `max_files` 见 6.2，`allowed_hosts` 见 6.3，许可的 `name` / `url` / `attribution` 见第 4 节；核查完之前一律 `usage_scope: internal_only`、`redistribution: unverified`、`verified_at: null`。来源开关在调度状态里，默认关闭，本任务不打开。
- 不新增依赖，不改 `pyproject.toml`、`uv.lock`；仓库是公开的，不写账号、密钥、本机绝对路径；临时文件放 `tmp_path`。

# 要求
1. **jp-mhlw**（`kind: mhlw_ndb`）：
   - yaml：`allowed_hosts: [www.mhlw.go.jp]`，`rate_per_sec: 0.2`，`schedule: weekly`；`scope` 写 ZIP 的 URL 和要取的 ZIP 内文件名（「08_特定健診_問診項目/標準的な質問票（質問項目１）　都道府県別性年齢階級別分布.xlsx」，含全角数字和全角空格，按原样匹配）；单文件上限 25 MiB。许可 PDL1.0，署名按决策文档 4.5。
   - 采集下载整个 ZIP；解析时用 `_zip.py` 取出指定文件，找不到就失败并写明文件名，不退回取别的文件。
   - 解析：每个 都道府県 × 性别 × 年龄阶级 × 回答选项 一条 `Observation`；`-` 遮蔽 → `value` 为空、`note` 写“遮蔽（集计单位小于 10 或防反推）”，绝不补零；`survey` 写“第11回NDBオープンデータ 特定健診”，时间为令和 5 年度（2023-04-01～2024-03-31）。
2. **jp-abr**（`kind: abr_town`）：
   - yaml：`allowed_hosts: [data.address-br.digital.go.jp]`（不泛放 `amazonaws.com`），`rate_per_sec: 0.2`，`schedule: weekly`；`scope` 写市町村代码 `134023` 和下载 URL；许可在核对完之前按 `internal_only`，`notes` 写“条目元数据 CC BY 4.0，v1 记为 PDL1.0，待核对（决策文档 8.2）”。
   - 解析：CSV 所有列按字符串读（全国地方公共団体コード、町字 ID 的前导零不丢）；每个町字一行写进块（町字名、读音、起止日期等），一个市町村一个 Document；空值保留为空，不填默认值。
3. **测试**：
   - NDB：样本 xlsx 里某个都道府県、性别、年龄阶级的值与文件一致；`-` 的格子 `value` 为空、`note` 非空；用 `tmp_path` 构造带原文件名（全角字符）的 ZIP，断言能取出，文件名差一个字符就失败。
   - ABR：样本 ZIP 解出的町字数、前导零、日文字符原样。
   - `test_jp_files2_e2e.py`：两个来源各自离线入库，NDB 用 `query_sql` 查一行并给出处，ABR 用“青ヶ島村”检索到块。
4. `summary.md` 分两个来源各写验收结果。
5. `make lint` 0 条，`make check T=tests` 全部通过。

# 不做
- 不抓 NDB 的レセプト和其他表，不抓 ABR 的地番、住居表示、地图。
- 不对遮蔽值做推算。

# 补充：来源目录文件（2026-10-31）
- 新来源要同时提交 `catalog/jp-mhlw.json`、`catalog/jp-abr.json`（仓库里登记来源的导出目录，`tests/core/test_registry_export.py` 和 `tests/integration/test_tachibana_m2.py` 要求它和 `sources/*.yaml` 一致）。用 `uv run --frozen rikolto registry export --out catalog` 生成，只提交本任务来源的文件；`catalog/` 下其他文件不应有变化，有变化就说明改到了别的来源，要查清楚。
- `tests/regressions/test_audit_wisteria_102.py` 已在 `feature/0.4` 上改为只固定原有的 7 个来源（提交 42efc70），本任务不需要、也不许改它。
