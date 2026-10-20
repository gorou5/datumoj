# DATUMOJ-IVY-001 完成说明：契约一（数据模型、locator、工具输出格式）

## 验收
- `make check T=tests/core/test_models.py` → `98 passed`
- 全量 `pytest -q`（用 `.venv/bin/pytest -q`）→ `99 passed`，没有破坏已有测试
- 测试先写，先看到失败（`ImportError: cannot import name 'models'`），再写实现

## 变更文件
- `src/datumoj/core/models.py`（新增）：全部模型与纯函数
- `tests/core/test_models.py`（新增）：98 个用例
- `schemas/*.schema.json`（新增 22 个）：`export_schemas("schemas")` 的导出结果
- `results/DATUMOJ-IVY-001/regen_schemas.py`：重新导出 schemas 用的辅助脚本（agent 环境只能跑 pytest，所以写成 pytest 用例；
  有 python 时直接用 `python -m datumoj.core.models schemas`）
- `results/DATUMOJ-IVY-001/summary.md`（本文件）

## 对照要求
1. pydantic v2 模型：`Source`、`Dataset`、`Resource`、`Snapshot`、`Document`、`Chunk`、`Observation`、`Citation`，
   另加 `LicenseInfo`、`Period`。全部 `frozen`，`extra="forbid"`（拼错字段名直接报错）。
2. `FetchOutcome`：8 个值，和要求完全一致。
3. `Snapshot`：要求列出的 13 个字段都有。`fetched_at` 必须带时区；`resource_url` 必须已去 Key（`redact_url(url) == url`），
   且只接受 http(s)。`ok` 时 sha256、size 必填；`http_error`/`network_error`/`too_large`/`blocked_by_robots` 时 sha256 必须为空、
   error 必填；`http_error` 还要求 http_status。
4. `Chunk`：9 个字段齐全；`doc_key` 校验为 `<source_id>:<来源内 ID>`；有只读属性 `source_id`。
5. `Observation`：字段集合**正好**是要求的 20 个；`survey` 必填且不能为空白；`value` 是 `Decimal | None`（拒绝 NaN/Inf，精度保留，
   JSON 里序列化成字符串）；`value` 为空时 `note` 必须非空；另校验 `time_start <= time_end`、`dataset_key` 前缀等于 `source_id`。
6. locator：`parse_locator` / `format_locator`，13 个 key，往返一致，保留顺序。另加 `locator_has_prefix`（按段比较，供评测按前缀判定命中）。
   模型字段用 `Locator` 类型，JSON Schema 里带同一个正则（`LOCATOR_PATTERN`）。
7. `Citation`：要求的全部字段；`time_basis` 必填。三个时间字段都空 ⇔ `no_data_date`；选了 `period`/`published`/`effective`
   就必须有对应日期。`render()` 一行输出：来源 | 时间口径 | 抓取日 | URL | 快照前 12 位 | 位置 | 许可 | 署名 | 使用范围；
   `no_data_date` 时写“无数据日期”，抓取日只出现在“抓取日”标签后（测试检查抓取日期只出现一次）。字段里的换行会被压成空格。
8. `UsageScope`、`Redistribution` 枚举；约束放在 `LicenseInfo` 上（`Source.license` 用它）。
9. `redact_url`：见下面“假设”。
10. 工具输出：`SourceText`（`content_role` 是 `Literal["untrusted_source_text"]`，传别的值报错）、`Hit`（`snippet` 只能是 `SourceText`，
    传普通字符串报错）、`GapState`、`Freshness`、`ToolResult[T]`。
11. `export_schemas(dir)`：每个类型一个 `<名字>.schema.json`，并删掉目录里过时的 `*.schema.json`；导出结果已放进 `schemas/`。
12. 测试覆盖：locator 往返、非法 key 及各种格式错误；Observation 空值必须带注记；redact；JSON Schema 和已提交文件逐字节一致、
    关键约束（survey 必填、content_role 常量、locator 正则、枚举值）确实出现在 schema 里。

## 假设和自己定的地方（请审查时重点看）
- **locator 值的转义**：值里出现 `%`、`;`、`=` 时转义成 `%25`、`%3B`、`%3D`（只有这三种，必须大写），其他字符（日文、空格、`/`、`:`）原样保留。
  这样 ZIP 成员路径之类的值也能放进 locator，并且编码唯一，往返一致。另外拒绝：空值、值首尾空白、控制字符、重复 key、空段（`art=1;`）。
  key 区分大小写。
- **可再分发的约束比要求更严**：要求是“`unverified` 时 `usage_scope` 只能是 `internal_only`”。我实现成“`redistribution` 不是 `allowed`
  时都只能是 `internal_only`”（`forbidden` 也一样），和 DATUMOJ-IVY-104 第 4 条一致。
- **redact_url 的参数名单**：`appid`、`app_id`、`api_key`、`apikey`、`api-key`、`key`、`token`、`access_token`、`auth`、`auth_token`、
  `secret`、`client_secret`、`password`、`passwd`、`pwd`、`signature`、`sig`、`subscription-key`，以及以 `_key`、`-key`、`apikey`、
  `_token`、`-token`、`_secret`、`-secret`、`password` 结尾的参数名；不区分大小写。`keyword`、`keys` 这类**不会**被误伤（e-Gov 法令关键词接口要用）。
  userinfo 里的密码、fragment 里的同类参数也会换掉。其余部分逐字保留、不重新编码，所以对已去 Key 的 URL 是恒等变换，模型据此校验 URL。
- **`granularity`** 理解为**时间粒度**，定成枚举 `TimeGranularity`：`year`、`fiscal_year`、`half_year`、`quarter`、`month`、`week`、`day`、`other`。
  地区粒度由 `area_code` 体现。如果原意是地区粒度或两者都要，需要改契约。
- **`Observation.unit` 和 `value` 是“必须显式给出、可以为 null”**（没有默认值）。PLAN 第 12 节要求观测值带单位，但有些表确实没写单位，
  所以没有强制非空，只强制解析器显式写 `unit=None`。
- **`ToolResult` 多了一个可空字段 `gap_detail`**：DATUMOJ-IVY-503 要求 `fetch_failed` 附原因、并给出最近的可查内容，外壳里需要一个地方放。
  `gap_state=fetch_failed` 时 `gap_detail` 必填。
- **`ToolResult` 拒绝 data 里出现 `Chunk` 实例**（递归检查列表、字典和嵌套模型），因为 `Chunk.text` 是未标注的来源正文。工具层要把正文包进
  `SourceText` 再返回。这只能拦住“直接塞 Chunk”，拦不住把正文当普通字符串塞进 dict；那部分靠类型（`Hit.snippet: SourceText`）和审查。
- `Hit` 额外校验：`locator == citation.locator`、`doc_key` 的来源等于 `citation.source_id`、`matched_by` 非空且不重复（取值 `keyword`/`vector`）。
- `Freshness`：`as_of`（结果所用快照里最新的抓取时间，即数据截止时间）、`oldest_fetched_at`、`by_source`（source_id → 抓取时间），都必须带时区。
- `source_id` 用 DATUMOJ-IVY-104 里的正则 `^[a-z]{2,4}(-[a-z0-9]+)+$`；`Source.country` 是 2–4 位小写（`jp`、`eu`、`intl`）。
- `Snapshot` 的 `not_modified`、`empty_body`、`spa_shell` 不约束 sha256（304 没有正文；空正文、SPA 外壳可能存了 raw 当证据）。
- `Dataset.license`、`Resource.license`、`Snapshot.license` 都是字符串，**原样记录**来源给的值（CKAN 常见 unknown 或互相矛盾）；
  核对过的许可结论在 `Source.license: LicenseInfo`。
- 额外导出了 `SearchResult.schema.json`（即 `ToolResult[list[Hit]]`），方便 MCP 线直接引用。

## 没做的
- 按任务要求，不写存储、HTTP、manifest。
- 没跑 `make lint`（ruff 不在本环境放开的命令里）。代码按 120 列写，审查时可以顺手跑一次。

## 遗留问题
- JSON Schema 里 `Decimal` 是 `number | string`，没带数字格式的正则；严格的校验在 pydantic 一侧。
- 部分约束（Citation 的时间口径、Snapshot 的成败一致性、Observation 空值注记）是 pydantic 的 model_validator，JSON Schema 表达不了，
  只有通过 pydantic 构造时才会校验。
