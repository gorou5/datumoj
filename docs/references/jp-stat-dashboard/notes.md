# 接口笔记：統計ダッシュボード WebAPI

## 证据标签与范围

标签 S1-S10 是整理本资料时所用的保存页面和请求响应记录的编号；这些记录未随本包提交，官方页面的 URL 和核对日期见各条正文与 terms.md。
S1 代表较早的利用规约摘要；S2 为端点/参数规格摘要；S3 为采集计划和差距记录；S4 为真实请求响应及其抓取索引；S5 为 URL 与哈希记录；S6 为 2026-10-19 保存的官方页面、规格、代码表和请求响应；S7 为 2026-10-20 更新的来源说明；S8 为同日 robots 请求策略和响应记录；S9 为同日直接读取的官方 API 页面、robots 路径 404 页面及公共数据利用规约页面；S10 为数据源与接口覆盖摘要。

- **真实请求验证过**：只用于明确标为此级的两份已保存 HTTP 响应；并非本次联网请求。
- **读过官方页面（日期）**：仅用于标明日期的已保存官方页面直接读取。
- **仅读文档**：官方规格文本、代码表或已保存页面中的静态内容；未以真实响应验证行为。
- **推测**：由已标明的字段或文档作出的有限解释，不是服务端保证。

## 1. 服务与端点

统计ダッシュボード WebAPI 提供 JSON、XML、CSV；getData 另列 JSON-stat。请求为 HTTP GET，官方页面列出六个功能。以下 JSON URL 以官方文档的形式列示；`Xml`、`Csv`（及 getData 的 `JsonStat`）替换格式段时，只在规格列出的组合中使用。[读过官方页面（2026-10-20）｜S9；仅读文档｜S2,S6]

| 功能 | JSON 端点 | 用途 | 验证级别 / 证据 |
|---|---|---|---|
| `getIndicatorInfo` | `https://dashboard.e-stat.go.jp/api/1.0/Json/getIndicatorInfo` | 指标 / 系列目录与元数据 | 仅读文档 S2,S6；真实请求验证过 S4 |
| `getRegionInfo` | `https://dashboard.e-stat.go.jp/api/1.0/Json/getRegionInfo` | 地域目录与地域元数据 | 仅读文档 S2,S6 |
| `getTermInfo` | `https://dashboard.e-stat.go.jp/api/1.0/Json/getTermInfo` | 术语 | 仅读文档 S2,S6 |
| `getSocialEventInfo` | `https://dashboard.e-stat.go.jp/api/1.0/Json/getSocialEventInfo` | 社会事件 | 仅读文档 S2,S6 |
| `getStatInfo` | `https://dashboard.e-stat.go.jp/api/1.0/Json/getStatInfo` | 统计调查目录 | 仅读文档 S2,S6 |
| `getData` | `https://dashboard.e-stat.go.jp/api/1.0/Json/getData` | 按指标、地域及时间条件取数 | 仅读文档 S2,S6；真实请求验证过 S4 |

## 2. 参数

下表的 URL 值类型为查询参数；规格未声明更细的编程语言类型时，不推定为数值或枚举。逗号列表以逗号分隔；文本值需按 UTF-8 URL 编码。每行的证据级别和来源均列在末两列。[仅读文档｜S2,S6]

### 通用参数

| 参数 | 类型 / 必填 | 允许值、默认值与边界 | 级别 / 证据 |
|---|---|---|---|
| `Lang` | 字符串；可选 | `JP` 或 `EN`；默认 `JP` | 仅读文档 S2,S6 |
| `callback` | 字符串；可选 | 任意函数名；元数据功能仅 JSON；getData 为 JSON / JSON-stat；用于 JSONP。规格注明因 CORS 支持而不推荐 | 仅读文档 S6 |
| `modifiedFrom` | 字符串；可选 | `yyyymmdd`，信息更新时间起点 | 仅读文档 S2,S6 |
| `modifiedTo` | 字符串；可选 | `yyyymmdd`，信息更新时间终点 | 仅读文档 S2,S6 |

### `getIndicatorInfo`

| 参数 | 类型 / 必填 | 允许值、默认值与边界 | 级别 / 证据 |
|---|---|---|---|
| `IndicatorCode` | 字符串逗号列表；可选 | 系列代码；最多 50 个 | 仅读文档 S2,S6 |
| `Category` | 字符串；可选 | 小分类代码：0101, 0102, 0201, 0202, 0203, 0204, 0301, 0302, 0303, 0304, 0305, 0401, 0403, 0404, 0501, 0502, 0601, 0603, 0701, 0702, 0703, 0704, 0705, 0706, 0801, 0802, 0901, 0902, 0903, 0904, 1001, 1003, 1101, 1102, 1201, 1202, 1203, 1301, 1302, 1304, 1401, 1402, 1403, 1404, 1405, 1501, 1502, 1503, 1504, 1505, 1601, 1602, 9999 | 仅读文档 S6 |
| `Time` | 字符串；可选 | `yyyymm00`、`yyyy1Q00`…`yyyy4Q00`、`yyyyCY00`、`yyyyFY00`；`99999999` 最新值标记仅 JSON-stat | 仅读文档 S2,S6 |
| `TimeFrom` / `TimeTo` | 字符串；可选 | 时间范围端点；二者须使用相同格式，格式不一致为错误 106 | 仅读文档 S2,S6 |
| `Cycle` | 整数；可选 | 1 月、2 季、3 年、4 财年 | 仅读文档 S2,S6 |
| `RegionalRank` | 整数；可选 | 1 国、2 日本全国、3 都道府县、4 市区町村 | 仅读文档 S2,S6 |
| `IsSeasonalAdjustment` | 整数；可选 | 1 原数值，2 季节调整值 | 仅读文档 S2,S6 |
| `StatCode` | 字符串；可选 | 统计调查代码；代码表见官方规格附件 | 仅读文档 S2,S6 |
| `StatName` | 字符串；可选 | 调查名称筛选；规格未给出长度上限 | 仅读文档 S2,S6 |
| `SearchIndicatorWord` | 字符串；可选 | 指标文字搜索；URL 编码 UTF-8 | 仅读文档 S2,S6 |

### `getRegionInfo`

| 参数 | 类型 / 必填 | 允许值、默认值与边界 | 级别 / 证据 |
|---|---|---|---|
| `RegionCode` | 字符串逗号列表；可选 | 最多 50 个；规格示例为 5 位本地代码或国家层级 3 位 ISO 3166-1 代码；`00000` 为全国示例 | 仅读文档 S2,S6 |
| `ParentRegionCode` | 字符串；可选 | 5 位父级地域代码；用于列出其子级 | 仅读文档 S2,S6 |
| `Time`, `TimeFrom`, `TimeTo` | 字符串；可选 | 与上方时间格式相同；起止格式须一致 | 仅读文档 S2,S6 |
| `RegionLevel` | 字符串逗号列表；可选 | 1 国；2 全国（日本）；3 都道府县；4 市区町村等；5 支厅/振兴局；6 郡；7 特别区部；8 政令指定都市；9 市；10 特别区；11 区；12 町；13 村。多值按 OR 查询 | 仅读文档 S6 |
| `SearchRegionWord` | 字符串；可选 | 地域名称文字搜索；规格未给出长度上限 | 仅读文档 S2,S6 |

### `getTermInfo`

| 参数 | 类型 / 必填 | 允许值、默认值与边界 | 级别 / 证据 |
|---|---|---|---|
| `Category` | 字符串；可选 | 与 `getIndicatorInfo` 的小分类代码相同 | 仅读文档 S2,S6 |
| `IndicatorCode` | 字符串；可选 | 指标 / 系列代码 | 仅读文档 S2,S6 |
| `StatCode` | 字符串；可选 | 统计调查代码 | 仅读文档 S2,S6 |
| `SearchTermWord` | 字符串；可选 | 术语文字搜索 | 仅读文档 S2,S6 |

### `getSocialEventInfo`

| 参数 | 类型 / 必填 | 允许值、默认值与边界 | 级别 / 证据 |
|---|---|---|---|
| `Time`, `TimeFrom`, `TimeTo` | 字符串；可选 | 与上方时间格式相同 | 仅读文档 S2,S6 |
| `SocialEventLevel` | 整数；可选 | 1 低、2 中、3 高 | 仅读文档 S2,S6 |
| `Category` | 字符串；可选 | 与官方小分类代码表相同 | 仅读文档 S2,S6 |

### `getStatInfo`

| 参数 | 类型 / 必填 | 允许值、默认值与边界 | 级别 / 证据 |
|---|---|---|---|
| `IndicatorCode` | 字符串；可选 | 指标代码，最多 50 个 | 仅读文档 S2,S6 |
| `StatCode` | 字符串；可选 | 统计调查代码 | 仅读文档 S2,S6 |
| `SearchSurveyWord` | 字符串；可选 | 调查名称文字搜索 | 仅读文档 S2,S6 |

### `getData`

| 参数 | 类型 / 必填 | 允许值、默认值与边界 | 级别 / 证据 |
|---|---|---|---|
| `IndicatorCode` | 字符串逗号列表；必填 | 19 位系列代码；每次最多 5 个 | 仅读文档 S2,S6；真实请求验证过 S4（单代码） |
| `RegionCode` | 字符串逗号列表；可选 | 最多 50 个；地域代码规则见 `getRegionInfo`；未指定时返回所有地域 | 仅读文档 S2,S6；真实请求验证过 S4（单地域） |
| `ParentRegionCode` | 字符串；可选 | 5 位父级代码；与规格一致的地域筛选参数 | 仅读文档 S2,S6 |
| `RegionLevel` | 字符串逗号列表；可选 | 同上，多个值 OR | 仅读文档 S2,S6 |
| `Time` | 字符串；可选 | 同上；单个时间码 | 仅读文档 S2,S6；真实请求验证过 S4（`2020CY00`） |
| `TimeFrom` / `TimeTo` | 字符串；可选 | 同格式时间范围；端点格式不匹配错误 106 | 仅读文档 S2,S6 |
| `Cycle` | 整数；可选 | 1 月、2 季、3 年、4 财年 | 仅读文档 S2,S6；真实请求验证过 S4（3） |
| `RegionalRank` | 整数；可选 | 1 国、2 全国（日本）、3 都道府县、4 市区町村 | 仅读文档 S2,S6；真实请求验证过 S4（3） |
| `IsSeasonalAdjustment` | 整数；可选 | 1 原数值，2 季节调整值 | 仅读文档 S2,S6；真实请求验证过 S4（1） |
| `StatName` | 字符串；可选 | 调查名称文字筛选 | 仅读文档 S2,S6 |
| `ValueCondition` | 字符串；可选 | 仅 `<N` 或 `>N`，为严格小于 / 大于；规格未列等于筛选语法 | 仅读文档 S2,S6 |
| `MetaGetFlg` | 字符串；可选；默认 `N` | `Y` / `N`；Y 请求类别元数据 | 仅读文档 S2,S6；真实请求验证过 S4（Y） |
| `SectionHeaderFlg` | 整数；可选；默认 1 | 1 输出 `RESULT`，2 不输出 | 仅读文档 S2,S6 |
| `modifiedFrom` / `modifiedTo` | 字符串；可选 | `yyyymmdd` 更新日期筛选；规格将其描述为信息更新时间，不是观测时间 | 仅读文档 S2,S6 |

参数的大小写敏感；不得从跨功能响应参数字段的大小写推导统一命名。文档对 `getData` 的 `metGetFlg` 与样例/参数表的 `MetaGetFlg` 有拼写大小写差异，提交请求时以示例和已验证样例中的 `MetaGetFlg` 为依据；JSON 响应里的 `getIndicatorInfo` 参数字段名 `IndicatorCode` 与 `getData` 的 `indicatorCode` 大小写不同。[仅读文档｜S2,S6；真实请求验证过｜S4]

### 查询边界、字符与分页

- getData 指标最多 5 个，地域最多 50 个；超限对应结果码 104。[仅读文档｜S2,S6]
- `Time` 为 8 字符格式；`99999999` 最新值标记仅 JSON-stat。`TimeFrom` 和 `TimeTo` 的格式须一致；不一致结果码 106。[仅读文档｜S2,S6]
- 结果总数上限为 100,000；超过时结果码 400。规格没有分页参数，也没有页码 / 偏移参数；不应假定可续取超限结果。[仅读文档｜S2,S6]
- 元数据查询也列有最多 50 个 IndicatorCode 或 RegionCode 的限制；具体功能见上表。没有依据说明超出上限会自动拆批。[仅读文档｜S2,S6]
- 规格示例要求日文搜索字词按 UTF-8 URL 编码；JSON 全角字符会 Unicode-escape，普通 JSON parser 可解码。[仅读文档｜S2,S6]
- 自动请求速率没有公布的数值配额。本资料示例和手动获取建议按不超过 1 次请求 / 秒操作；这是保守建议，不是官方阈值；`sources/jp-stat-dashboard.yaml` 当前设定的抓取速率为 0.5 次/秒，实际抓取以该文件为准。[推测｜S7,S9]

## 3. 响应形状、状态与时间字段

### JSON 字段

- 五个目录功能的 root 分别为 `GET_META_INDICATOR_INF`、`GET_META_REGION_INF`、`GET_META_TERM_INFO`、`GET_META_SOCIAL_INFO`、`GET_META_STAT_INFO`；规格结构均含 `RESULT`、`PARAMETER` 和 `METADATA_INF.CLASS_INF`，具体类别字段随功能变化。真实指标元数据响应含 `RESULT { status, errorMsg, date }`、`PARAMETER { lang, IndicatorCode }`、`CLASS_INF.CLASS_OBJ[] { @name, @code, details.detail[] { @code, @name, $ }, CLASS[] }`。[仅读文档｜S2,S6；真实请求验证过｜S4]
- getData 的 root 为 `GET_STATS`。响应中有 `RESULT`、`PARAMETER`、`STATISTICAL_DATA.RESULT_INF`、`TABLE_INF`、`CLASS_INF` 及 `DATA_INF.DATA_OBJ[]`。[仅读文档｜S2,S6]
- `RESULT` 有 `status`、`errorMsg`、`date`；真实响应两例为 status 0。该字段 `date` 是响应生成 / 请求处理时间，不能代替观测时间。[真实请求验证过｜S4；仅读文档｜S2,S6]
- 规格中的 `RESULT_INF` 字段为 `TOTAL_NUMBER`、`FROM_NUMBER`、`TO_NUMBER`；规格称 `FROM_NUMBER` 固定为 1，`TO_NUMBER` 等于总数。S4 的真实 JSON 仅观察到 `TOTAL_NUMBER`，未观察到 `FROM_NUMBER` / `TO_NUMBER`；规格未列可调用的分页偏移。[真实请求验证过｜S4；仅读文档｜S2,S6]
- `TABLE_INF.STAT_NAME[]` 的项含 `@code` 与 `$`；样例的 `$` 是调查 / 统计名称，不是系列来源机构的明确声明。[真实请求验证过｜S4]
- `CLASS_INF.CLASS_OBJ[]` 描述八类维度：`indicator`、`unit`、`regionCode`、`time`、`cycle`、`regionalRank`、`isSeasonal`、`isProvisional`；对象字段含 `@id`，真实样例还含 `@name`、`@sname`、`CLASS[]`。指标维度的 `CLASS[]` 可含 `@code`、`@name`、`@sname`、`TERM_DETAILS.TERM_DETAIL[] { @code, @name, $ }` 和 `INDI_ANNOTATIONS[]`；其他维度字段按规格各异，常见为 `@code` 与 `$`。[仅读文档｜S2,S6；真实请求验证过｜S4]
- `DATA_INF.DATA_OBJ[].VALUE` 含 `@indicator`、`@unit`、`@stat`、`@regionCode`、`@time`、`@cycle`、`@regionRank`、`@isSeasonal`、`@isProvisional` 与 `$`；`$` 的 JSON 值是字符串，即使内容表示数值；`CELL_ANNOTATIONS.$` 为可选单元格注记。[仅读文档｜S2,S6；真实请求验证过｜S4]
- 指标元数据还包含 `FROM_DATE` / `TO_DATE` 等起止时间信息；实际字段应按各功能的规格结构解析。[仅读文档｜S2,S6]
- 真实响应的 `VALUE.@time` 为 `2020CY00`；这是观测时间码。没有在样例或规格中找到每条值对应的发布时间戳。[真实请求验证过｜S4；仅读文档｜S2,S6]

### 状态码

以下为官方结果码表所记 HTTP 状态及含义；除两个 status 0 样例外，本任务没有真实请求验证这些响应。[仅读文档｜S2,S6；真实请求验证过｜S4]

| 结果码 | HTTP | 规格含义 | 证据级别 |
|---|---:|---|---|
| `0` | 200 | 正常取得结果 | 真实请求验证过 S4 |
| `1` | 200 | 未找到符合条件的数据 | 仅读文档 S2,S6 |
| `101` | 400 | 未指定必要参数 | 仅读文档 S2,S6 |
| `103` | 400 | 参数值错误 | 仅读文档 S2,S6 |
| `104` | 400 | 参数指定超过上限 | 仅读文档 S2,S6 |
| `105` | 400 | Time 的长度错误 | 仅读文档 S2,S6 |
| `106` | 400 | TimeFrom / TimeTo 格式不一致 | 仅读文档 S2,S6 |
| `107` | 400 | ValueCondition 格式错误 | 仅读文档 S2,S6 |
| 空白 | 400 | modifiedFrom / modifiedTo 日期格式错误 | 仅读文档 S2,S6 |
| `200` | 500 | 数据库错误 | 仅读文档 S2,S6 |
| `299` | 500 | 其他处理错误 | 仅读文档 S2,S6 |
| `301` | 200 | ValueCondition 筛选后为零条 | 仅读文档 S2,S6 |
| `400` | 200 | 结果超过 100,000 件 | 仅读文档 S2,S6 |
| `404` | 404 | URL 不存在 | 仅读文档 S2,S6 |

`status=1`、`301` 和 `400` 是文档中的不同情况；不能统一解释为相同的空结果。S4 两个成功请求都为 0；没有真实空结果或错误响应样例。[仅读文档｜S2,S6；真实请求验证过｜S4]

### 时间与 as_of

`VALUE.@time` 或 CLASS 中的时间 `@code` 是观测时段，`RESULT.date` 是请求响应时间；`modifiedFrom` / `modifiedTo` 是信息更新时间筛选，不是观测时间。对 datumoj 的 provenance，可将 `@time` 保存为源观测时间码，将请求时刻单独记录为 retrieval time；本 API 的结果未显示通用的系列发布时间戳或修订版本号。[真实请求验证过｜S4；仅读文档｜S2,S6；推测｜S4,S6]

regional rank 1-4 分别是国、全国（日本）、都道府县、市区町村；季节调整码 1 为原数值、2 为季节调整值。一个 IndicatorCode 可能包含多个 Cycle / RegionalRank / IsSeasonalAdjustment 系列要素；请求参数须对应实际存在的组合，样例只验证了都道府县 rank 3、年周期 3、原数值 1。[仅读文档｜S2,S6；真实请求验证过｜S4]

## 4. 无样本时的手动获取形状

以下为占位 URL 形状，不含实际凭证；官方说明无需 Key / 注册。将 `<...>` 替换为从目录解析出的代码，参数值按 UTF-8 URL 编码。此处是操作形状，不表示本次发送了请求。[读过官方页面（2026-10-20）｜S9；仅读文档｜S2,S6]

```text
https://dashboard.e-stat.go.jp/api/1.0/Json/getIndicatorInfo?Lang=JP&IndicatorCode=<19-digit-indicator-code>
https://dashboard.e-stat.go.jp/api/1.0/Json/getData?Lang=JP&IndicatorCode=<19-digit-indicator-code>&RegionCode=<region-code>&Time=<time-code>&Cycle=<1-4>&RegionalRank=<1-4>&IsSeasonalAdjustment=<1-2>&MetaGetFlg=Y&SectionHeaderFlg=1
```

建议人工、低频地逐个取得目录和所需序列，并控制在每秒不超过 1 个请求；这是本资料的保守建议，官方页面未公布数值 QPS。[推测｜S7,S9]

## 5. 与 DATUMOJ 计划的对照

- 计划的 P0 `jp-stat-dashboard` 与指标目录 + 精选指标地区时间序列方向相符；API 目录与 getData 规格覆盖这两类功能。[仅读文档｜S2,S3,S6]
- “无需 Key”与官方页面一致：API 无需利用登记；S4 两个 URL 没有凭证参数，返回 status 0。[读过官方页面（2026-10-20）｜S9；真实请求验证过｜S4]
- 计划所述 getData 每次最多 5 个指标、50 个地区与规格相符。没有分页；规格另有全体 100,000 件上限及错误码 400；超限后如何自动切分没有证据，计划中的 batch planner 仍有必要。[仅读文档｜S2,S3,S6]
- 计划提到 `modifiedFrom` 增量筛选；规格确有 `modifiedFrom` / `modifiedTo`，但将它们定义为信息更新时间筛选而非观测时间。筛选边界、是否涵盖各类值更新、删除记录及完整增量语义没有被样例验证。[仅读文档｜S2,S3,S6]
- 计划中的 stat_dashboard fixture 任务（见 TASKS.md 中对应任务的样例与批量 getData 响应）是计划项；本次没有生成或复制 fixture。S4 两份真实响应只可证明一次单指标、单地域请求成功，不证明 5/50 批量请求。[仅读文档｜S3；真实请求验证过｜S4]
- 计划将数据存为带 provenance 的 observations。现有 `@time` 可提供观察时段代码，`RESULT.date` 可提供请求处理时点；没有逐系列更新时间戳或修订号证据，因此两者需分字段保存，不能将请求时间当作观测时间。[仅读文档｜S2,S3,S6；推测｜S4,S6]

## 6. 未验证与未找到

- **UNVERIFIED**：getData 超过 5 指标或 50 地域、以及超过 100,000 结果时的实际响应；查阅 S2/S6 的参数和结果代码表，没有在 S4 中发起边界请求。[仅读文档｜S2,S4,S6]
- **UNVERIFIED**：modifiedFrom / modifiedTo 的包含边界、对每条数据更新的覆盖范围、删除 / 撤回通知及可重复增量完整性；查阅 S2/S6 规格和 S4 两个响应，未找到对应验证样例。[仅读文档｜S2,S4,S6]
- **UNVERIFIED**：实际空结果与错误响应体细节；S2/S6 记载结果码，S4 仅有 status 0。[仅读文档｜S2,S4,S6]
- **NOT FOUND**：页码、偏移、游标或“更多结果”续取标记；已查 S2 的全部端点/响应字段、S6 的规格及状态码表。[仅读文档｜S2,S6]
- **NOT FOUND**：单条观测数据的发布时间、更新时间或修订版本字段；已查 S2/S6 的 getData 结构和 S4 响应中的 `RESULT.date`、`VALUE.@time`。[真实请求验证过｜S4；仅读文档｜S2,S6]
- **UNVERIFIED**：各指标实际提供的地域 / 时间 / rank / seasonality 组合；文档定义维度编码，S4 仅验证大阪府单一组合。[仅读文档｜S2,S4,S6]
- **UNVERIFIED**：外部来源系列逐指标的权利、来源单位、可再分发性；细节见 terms.md。[仅读文档｜S1,S7,S9,S10]

## 7. 验证程度汇总

| 范围 | 验证程度 | 已有依据与边界 |
|---|---|---|
| 两个保存的请求响应：getData 与 getIndicatorInfo 的 status 0、返回字段、请求不带 Key | 真实请求验证过 | S4,S5；只覆盖各一条成功请求，不证明边界、空结果或失败行为 |
| 官方 API 入口、六个功能、无登记声明 | 读过官方页面（2026-10-20） | S9；本次为离线核对已保存页面，不是实时联网请求 |
| 参数、格式、默认值、上限、无分页、字段结构、结果码 | 仅读文档 | S2,S6；结果码表未逐码实际请求 |
| DATUMOJ 计划对照、外部来源系列条件、TTL/QPS 未公布、robots 404 | 仅读文档 / 读过官方页面（2026-10-20） | S3,S7,S8,S9,S10；robots 404 有保存响应元数据，无 robots 指令 |
| 每秒不超过一次的建议；把观测时段与抓取时间分字段保存 | 推测 | S4,S6,S7,S9；保守操作建议和 provenance 映射，不是服务端保证 |

