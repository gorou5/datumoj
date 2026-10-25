# e-Gov データポータル（jp-egov-data）资料笔记

## 证据标签

| 标签 | 官方页面 URL | 取得日期 | 验证程度 |
|---|---|---|---|
| S1 | https://data.e-gov.go.jp/info/ja/terms | 2026-10-20 | 读过官方页面（2026-10-20） |
| S2 | https://www.digital.go.jp/resources/open_data/public_data_license_v1.0 | 2026-10-20 | 读过官方页面（2026-10-20） |
| S3 | https://data.e-gov.go.jp/data/api_guide | 2026-10-20 | 读过官方页面（2026-10-20） |
| S4 | https://data.e-gov.go.jp/data/api/action/package_show?id=4aa798d6-afc9-487c-aeb5-822d0d1156b3 | 2026-10-20 | 真实请求验证过 |
| S5 | https://data.e-gov.go.jp/info/ja/service-policy | 2026-10-20 | 读过官方页面（2026-10-20） |
| S6 | https://data.e-gov.go.jp/info/ja/help | 2026-10-20 | 读过官方页面（2026-10-20） |
| S7 | https://data.e-gov.go.jp/info/utility-time | 2026-10-20 | 读过官方页面（2026-10-20） |
| S8 | https://data.e-gov.go.jp/data/dataset/mext_20170207_0037 | 2026-10-20 | 读过官方页面（2026-10-20） |
| S9 | https://data.e-gov.go.jp/robots.txt | 2026-10-20 | 真实请求验证过 |
| S10 | https://www.data.go.jp/robots.txt | 2026-10-20 | 真实请求验证过 |
| S11 | https://data.e-gov.go.jp/ | 2026-10-20 | 读过官方页面（2026-10-20） |

S1-S11 各标签指向本表列出的官方页面或已保存的请求结果（不随本包提交）；取得日期见表。

## 验证程度与入口

- e-Gov 数据门户入口说明其为中央行政开放数据门户，数据集条目可包含文件或 URL。（摘要，非引用）（读过官方页面（2026-10-20））〔证据:S11〕
- 元数据 API 说明称该 API 面向任何人开放；所列方法使用 GET，文档未列 Key 或认证参数。（读过官方页面（2026-10-20））〔证据:S3〕
- 文档给出的 action API 基址为 `https://data.e-gov.go.jp/data/api/action`；其接口说明称与 CKAN 标准相同，并说明不提供更新数据的 API。（读过官方页面（2026-10-20））〔证据:S3〕
- API 指南说明响应为 UTF-8，时间戳采用 ISO 8601，时区为 UTC；JST 使用时需转换。（读过官方页面（2026-10-20））〔证据:S3〕

## CKAN action API

| 方法 | 文档列出的参数与行为 |
|---|---|
| `package_search` | GET `/package_search`；`q`、`fq`、`fq_list`、`sort`、`rows`、`start`、facet 参数可见；文档没有给出 `rows` 的最大值。（读过官方页面（2026-10-20））〔证据:S3〕 |
| `package_show` | GET `/package_show`；必需参数为数据集 `id` 或 `name`，可选 `include_tracking`。（读过官方页面（2026-10-20））〔证据:S3〕 |
| `package_activity_list` | GET `/package_activity_list`；必需参数为数据集 `id`，另有 `limit` 与 `offset`；说明返回活动流但不含详情。（读过官方页面（2026-10-20））〔证据:S3〕 |
| `recently_changed_packages_activity_list` | GET `/recently_changed_packages_activity_list`；返回最近新增或变更数据集的活动流，参数有 `limit` 与 `offset`。（读过官方页面（2026-10-20））〔证据:S3〕 |
| `package_list` 与 `current_package_list_with_resources` | 两个方法均列有 `limit` 与 `offset`；`current_package_list_with_resources` 返回数据集及其资源列表。（读过官方页面（2026-10-20））〔证据:S3〕 |

- `package_search` 的 `start` 用于分段显示结果，`rows` 指定本次取得的数据集数；该指南没有记录最大页大小。（读过官方页面（2026-10-20））〔证据:S3〕
- 活动接口的 `limit` / `offset` 有参数说明与示例；保存的 API 指南没有给出这些参数的最大数值。（读过官方页面（2026-10-20））〔证据:S3〕
- 帮助页将元数据 API 说明指向 API 指南，并未补充另一个请求速率或并发值。（读过官方页面（2026-10-20））〔证据:S3,S6〕

## 已保存响应的形状与日期字段

- 一次保存的 `package_show` 响应顶层有 `help`、`success`、`result`；该响应的 `success` 为 `true`，`result` 内含数据集字段与 `resources` 数组。（真实请求验证过）〔证据:S4〕
- 这条响应包含一个资源；数据集级 `license_id` 为 `null`，该资源的 `license_id` 字符串为 `cc-by`。（真实请求验证过）〔证据:S4〕
- API 指南示例也在数据集与资源对象中展示许可字段；示例值包含不同大小写或名称，文档没有说明资源级字段的优先顺序或规范化规则。（读过官方页面（2026-10-20））〔证据:S3〕
- 保存的响应含 `metadata_created` 与 `metadata_modified`；指南说明 API 时间戳为 UTC，不能直接当作 JST。（真实请求验证过）〔证据:S3,S4〕
- 同一数据集的页面列出发布日 `2017-02-13`、最后更新日 `2023-09-20`、创建频率“不定期”；页面上的对象期间字段为空。（读过官方页面（2026-10-20））〔证据:S8〕
- 这些值来自一个数据集页面与一次 API 响应；它们不能证明全目录的日期字段都存在或采用相同含义。（真实请求验证过）〔证据:S4,S8〕
- 页面样本没有给出可作为统一数据期间的非空值；抓取时间不替代来源数据日期。（读过官方页面（2026-10-20））〔证据:S3,S8〕

## 自动访问、服务时间与速率

- 已读的门户规约、服务政策、帮助、API 指南与服务时间页面中，未找到自动抓取规则、请求速率或并发数值；这只描述本次读取范围。（读过官方页面（2026-10-20））〔证据:S1,S3,S5,S6,S7〕
- 服务时间页写明 e-Gov 域名服务原则上每日全天提供，同时提示维护可能造成暂时不可用；该说明没有给出采集时段或速率要求。（读过官方页面（2026-10-20））〔证据:S7〕
- `data.e-gov.go.jp/robots.txt` 的保存请求结果为 HTTP 404；`www.data.go.jp/robots.txt` 的结果也为 HTTP 404，均没有取得 robots 指令。（真实请求验证过）〔证据:S9,S10〕
- 404 或无 robots 内容不能单独证明自动抓取已获许可。（读过官方页面（2026-10-20））〔证据:S1,S3,S9,S10〕
- `catalog.data.go.jp/robots.txt` 的规则为 UNVERIFIED；已读 API 指南列出的 action 主机是 `data.e-gov.go.jp`，不能用其他主机的 404 推断该主机规则。（仅读文档）〔证据:S3,S9,S10〕

## 许可字段、第三方资源与待确认项

- 门户规约指向 PDL1.0，但保留权利另有标示的情形；PDL1.0 也提示第三方权利与外部数据库/API 提供方条件。（读过官方页面（2026-10-20））〔证据:S1,S2〕
- 保存响应显示数据集级与资源级均可见许可字段；这条样本不能证明数据集级 `license_id` 在整个目录都为空。（真实请求验证过）〔证据:S3,S4〕
- `license_id: cc-by` 的资源级值在保存页面中没有对应的许可正文或优先级解释；其字段含义与适用范围待确认。（仅读文档）〔证据:S1,S2,S3,S4〕
- 若资源文件由省厅、地方政府或其他外部站点托管，该文件的许可与是否可抓取是单独问题；本资料不据门户元数据作结论。（仅读文档）〔证据:S1,S2,S3,S4〕
- 门户规约引用的站点专属 PDL1.0 重要信息页未在本次保存材料中核对，其可能列出的例外仍为 UNVERIFIED。（读过官方页面（2026-10-20））〔证据:S1〕

## 与 DATUMOJ 计划的对照

| 字段 | 计划文件记载与证据对照 |
|---|---|
| `allowed_hosts` | `sources/jp-egov-data.yaml` 记载 `data.e-gov.go.jp`、`*.go.jp`、`*.lg.jp`；API 指南列出的 action 主机是 `data.e-gov.go.jp`，其 robots 请求为 404。（仅读文档）〔证据:S3,S9〕 |
| `rate_per_sec` | `sources/jp-egov-data.yaml` 记载 `0.5`；已读页面未找到官方速率数字，服务时间页只说原则上 24 小时、全年可用且维护时可能中断。（仅读文档）〔证据:S1,S3,S5,S6,S7〕 |
| `max_total_bytes`、`max_files`、`max_file_bytes` | `sources/jp-egov-data.yaml` 记载 `10737418240` 字节、`5000` 个文件、单文件 `209715200` 字节；已读页面没有给出与这些值对应的下载总量或文件数上限。（仅读文档）〔证据:S1,S3,S5,S7〕 |
| `activity_limit` 与 `formats` | 计划文件记载活动上限 `100`，文件格式白名单为 `csv`、`xlsx`、`xls`、`pdf`、`json`、`xml`；API 指南有活动 `limit` 参数，单条保存响应的资源格式为 `PDF`，但这些证据没有验证活动上限或完整格式白名单。（仅读文档）〔证据:S3,S4〕 |
| 许可字段 | 计划文件把许可名与核对日期留空、再分发标为未核实、`usage_scope` 记为 `internal_only`；已读门户规约指向 PDL1.0，样本则显示数据集许可值为空、资源值为 `cc-by`，但字段优先关系和外部文件条件仍未确认。（仅读文档）〔证据:S1,S2,S3,S4〕 |
| 署名与许可 URL | 计划文件记载许可 URL 为 `https://data.e-gov.go.jp/info/terms`、署名文字为 `出典：e-Govデータポータル（https://data.e-gov.go.jp/）`；本次读取的门户利用规约页位于 `/info/ja/terms`，其出典例使用 `e-Govポータル` 与 `https://www.e-gov.go.jp`。（读过官方页面（2026-10-20））〔证据:S1,S2〕 |

这些字段可据此核对。（仅读文档）〔证据:S1,S2,S3,S4,S5,S6,S7,S9〕
