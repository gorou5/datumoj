---
id: DATUMOJ-MAPLE-304
title: 国土数值信息 P05（佐賀）和 2024 经济普查表 1-1：命名文件下载、ZIP 安全解压与混合编码
project: DATUMOJ
status: ready
priority: 2
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/crawlers/ksj.py", "src/datumoj/rikolto/parsers/ksj.py", "src/datumoj/rikolto/parsers/_zip.py", "src/datumoj/rikolto/parsers/estat_tables.py", "src/datumoj/rikolto/crawlers/estat_files.py", "sources/jp-ksj.yaml", "sources/jp-census.yaml", "tests/parsers/test_zip_safe.py", "tests/crawlers/test_ksj.py", "tests/parsers/test_ksj.py", "tests/parsers/test_census2024.py", "tests/integration/test_jp_files_e2e.py", "results/DATUMOJ-MAPLE-304/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/**", "schemas/**", ".github/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/wiring.py", "src/datumoj/eltiro/**", "sources/README.md"]
protected: ["tests/fixtures/**", "tests/regressions/**"]
acceptance:
  automated:
    - cmd: make check T=tests/parsers/test_zip_safe.py
    - cmd: make check T=tests/parsers/test_ksj.py
    - cmd: make check T=tests/parsers/test_census2024.py
    - cmd: make check T=tests/integration/test_jp_files_e2e.py
    - cmd: make check T=tests/parsers/test_estat_dataset.py
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-304/summary.md 完成说明（两个来源分别写验收结果；ZIP 上限和编码处理的说明）"]
  review: {by: other_family, checklist: ["许可、署名、allowed_hosts、频率和上限是否和来源决策文档一致", "是否只请求白名单主机、遵守 robots，不绕过", "单位、时间、注记是否原样保留，没有补零或自行换算", "是否有联网的测试", "是否改了契约、wiring 或其他来源的实现", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
国土数值信息的市町村役场和公共集会设施（佐賀県）能按市町村检索；2024 经济普查各地区的事业所数和从业者数进 `query_sql`。两个来源分别验收。

# 背景
来源决策文档 3.3（KSJ、经济普查两节）、4.5、6.1–6.4 节；样本 `tests/fixtures/ksj/P05-22_41_GML.zip`（原样 ZIP，cp932 与 UTF-8 混合）和 `tests/fixtures/census2024/c1_001_1.xlsx`（表 1-1 原样）。
- 经济普查文件从 e-Stat 的 `file-download` 下载，和已有来源 `jp-estat-files` 同一个主机和下载方式；已有的 `estat_files` 采集器、`estat_tables` 解析器按 `stat_inf_id` 和版式（layout）处理 Excel。
- 决策文档 6.2：单文件上限 25 MiB、ZIP 解压后总量 250 MiB 由本任务实现（现在单文件默认等于 `max_total_bytes`）。
- 本任务新建的 `parsers/_zip.py` 也给 305 用。

计划见 `docs/plans/0.3-maple.md`（第 5、6.6 节）和来源决策 `docs/plans/0.3-maple-sources.md`（下文列出的节）。本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- **按插件接入**：`crawlers/` 加一个模块（`Crawler` 子类声明 `kind`），`parsers/` 加一个模块（`Parser` 子类声明 `source_kind`），再加 `sources/<id>.yaml`；`discover_crawlers` / `discover_parsers` 会自动发现，不改 `wiring.py`。写法参照已有的 `stat_dashboard`（API → 观测值）、`ckan`（目录 → Dataset）、`estat_files`（文件 → 观测值）。
- 契约（`core/contracts.py`、`core/models.py`）和 schema 不改；需要改时写进 `summary.md` 并停下来问。
- 样本在 `tests/fixtures/<目录>/`（H1，PR #32 已合并），只读；每个目录的 README 写了 URL、过滤参数、样本类型和许可。测试全部离线，HTTP 只用这些样本或 `pytest-httpserver`。
- yaml 的值按决策文档：`schedule` 见 6.1，`rate_per_sec` / `max_total_bytes` / `max_files` 见 6.2，`allowed_hosts` 见 6.3，许可的 `name` / `url` / `attribution` 见第 4 节；核查完之前一律 `usage_scope: internal_only`、`redistribution: unverified`、`verified_at: null`。来源开关在调度状态里，默认关闭，本任务不打开。
- 不新增依赖，不改 `pyproject.toml`、`uv.lock`；仓库是公开的，不写账号、密钥、本机绝对路径；临时文件放 `tmp_path`。

# 要求
1. **ZIP 安全解压** `parsers/_zip.py`（下划线开头，不被当成解析器插件）：
   - 拒绝绝对路径、`..`、符号链接条目；解压后总量超过 250 MiB 或条目数超过上限时整体失败，不写部分结果；不落盘，直接在内存里按条目读。
   - 文件名编码：没有 UTF-8 标志的条目名按 cp932 解码，失败再按 UTF-8，仍失败就报错（不乱码入库）。
   - 文本内容的编码按文件分别判断（KSJ 里 `.dbf`、`KS-META-*.xml` 是 cp932，`.geojson`、GML 是 UTF-8），由调用方指定或按 BOM / XML 声明判断，不全局猜。
2. **jp-ksj**（`kind: ksj_file`）：
   - yaml：`allowed_hosts: [nlftp.mlit.go.jp]`，`rate_per_sec: 0.2`，`schedule: weekly`；`scope.files` 写 P05-2022 佐賀（41）的下载 URL，单文件上限 25 MiB。
   - 解析：`KS-META-*.xml` 生成 `Dataset`（名称、年份、国土交通省、许可 CC BY 4.0）；GeoJSON 的每个设施按市町村分组，每个市町村一个块，块里写设施名、种别、所在地、经纬度；locator 写 ZIP 内文件名和要素序号。
3. **jp-census**：
   - yaml：沿用 `kind: estat_files`，`stat_inf_id: "000040389320"`，甲调查正式集计、表 1-1；`schedule: weekly`，`rate_per_sec: 0.2`；`notes` 写明“2024 年甲调查排除没有雇员的个人经营事业所，和往年口径不同”。
   - 表 1-1 的版式（全国、都道府県、市区町村 × 事业所数、从业者数）现有 layout 不认时，在 `estat_tables.py` 新增一个 layout；已有 layout 和 `tests/parsers/test_estat_dataset.py` 等已有测试不改、原样通过。
   - 地区代码按 5 位市区町村代码；表里的特殊值（`-`、`x`、`…` 等）进 `note`，不补零。
4. **测试**：
   - `test_zip_safe.py`：用 `tmp_path` 里构造的 ZIP 覆盖路径逃逸、符号链接、超量、cp932 文件名、UTF-8 文件名。
   - `test_ksj.py`：样本 ZIP 解出的市町村数和某个市的设施；cp932 的元数据 XML 正确解码。
   - `test_census2024.py`：佐賀県和某个市的事业所数、从业者数与样本一致；特殊值不补零。
   - `test_jp_files_e2e.py`：两个来源各自离线入库，KSJ 用“佐賀市 公民館”之类检索到块，经济普查用 `query_sql` 查到一个市并给出出处。
5. `summary.md` 分两个来源各写验收结果（决策文档 7.2：两个来源分别验收）。
6. `make lint` 0 条，`make check T=tests` 全部通过。

# 不做
- 不回补其他年份、其他县，不抓经济普查表 5（7.9MB，决策文档 3.3）。
- 不做空间检索（坐标只写进块和元数据）。
- shortcut：ZIP 整包读进内存，ZIP 本身受 25 MiB 单文件上限约束；要处理更大的 ZIP 时改流式。
