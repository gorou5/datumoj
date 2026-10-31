# e-Stat API（jp-estat-api）

| 项目 | 内容 |
|---|---|
| 提供者 | 37（jp-gov-data-api）团队 |
| 资料整理日期 | 2026-10-30（本次整理；仅读文档；S1-S8）。 |
| 资料核对日期 | 2026-10-30（读过官方页面；S1-S7）。维护者计划和 PR 摘录仅作仅读文档（S8）。 |
| 官方入口 | [API 功能利用规约](https://www.e-stat.go.jp/api/terms-of-use)（S1）；[Credit 显示](https://www.e-stat.go.jp/api/api-info/credit)（S2）；[站点利用规约](https://www.e-stat.go.jp/terms-of-use)（S3）；[利用指南](https://www.e-stat.go.jp/api/api-info/api-guide)（S4）；[提供数据](https://www.e-stat.go.jp/api/api-info/api-data)（S5）；[API 功能首页](https://www.e-stat.go.jp/api/)（S6）；[API FAQ](https://www.e-stat.go.jp/api/api-dev/faq)（S7）。 |
| 需要 Key | 未核对。现有页面表明使用前须注册、取得应用程序 ID 并在 API 请求中发送该 ID；是否另需 API Key 未说明。（api_terms_of_use.txt:8；api_terms_of_use.txt:9；api_terms_of_use.txt:12；api_terms_of_use.txt:13；api_guide.txt:12；api_guide.txt:14；api_guide.txt:15；api_guide.txt:16；S1、S4） |
| 许可 / 利用规约 | 本 API 的规约名称为「政府統計の総合窓口(e-Stat)API機能利用規約」（api_terms_of_use.txt:5；S1）。其第 6 条将内容利用条件指向「政府統計の総合窓口（e-Stat）利用規約」（api_terms_of_use.txt:27；S1）。下方对站点规约的文字只按已保存页面记录，未判定具体表或响应的权利状态。可否再分发：未核对。站点规约开头对其所称内容列出复制、公众传输、改编及商业利用文字（site_terms_of_use.txt:4；site_terms_of_use.txt:5；S3）；另有另行适用规则清单（site_terms_of_use.txt:38；site_terms_of_use.txt:39；site_terms_of_use.txt:40；site_terms_of_use.txt:70；S3），且内容可能含第三方权利（site_terms_of_use.txt:24；site_terms_of_use.txt:25；site_terms_of_use.txt:26；site_terms_of_use.txt:27；site_terms_of_use.txt:28；S3）。本资料不就 API 响应或任何具体统计表作许可判断。 |
| 本资料的可信度 | 仅读文档：依据 2026-10-30 持有的官方页面文字（S1-S7）。没有读取 API 规格页面、响应 schema、endpoint 内容、应用程序 ID 发放画面或测试表单，也没有进行实时 API 请求。本文是对持有页面的阅读记录，不是法律意见或许可认定。 |
| 对应的 DATUMOJ 任务 |  |
| 更新频率 | NOT STATED：FAQ 只说不同制作机构的更新时期不同，并请询问制作机构；没有逐表更新时刻表（api_faq.txt:81；S7）。 |

## 内容清单

- notes.md：证据标签、访问与 ID、Credit 与规约边界、访问限制、数据范围及日期。
- terms.md：Credit 与出处、利用条件与禁止事项、API 功能的另行规则范围问题及开放事项。
- PLAN_CHECK_TABLE.md（维护者侧表，未随本 PR 提交）：逐项对照维护者计划中的 e-Stat API 说法与已持有页面；S8 是维护者文本，不是官方来源。
