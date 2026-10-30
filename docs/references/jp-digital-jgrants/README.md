# Jグランツ 补助金信息取得 API（jp-digital-jgrants）

| 项目 | 内容 |
|---|---|
| 提供者 | 门户规约将数字厅列为 Jグランツ的运营、提供者。（读过官方页面（2026-10-30）；S3；portal_stipulation_excerpt.txt:13, portal_stipulation_excerpt.txt:17） |
| 资料整理日期 | 2026-10-30（本次整理）。（仅读文档；S1、S2、S3、S4） |
| 资料核对日期 | 2026-10-30（本次阅读所列官方材料与维护者提案）。（读过官方文档（2026-10-30）；S1、S2；读过官方页面（2026-10-30）；S3；仅读文档；S4） |
| 官方入口 | [Web-API 利用规约 PDF](https://fs2.jgrants-portal.go.jp/API%E5%88%A9%E7%94%A8%E8%A6%8F%E7%B4%84.pdf)（S1）；[API 利用概要 PDF](https://fs2.jgrants-portal.go.jp/API%E5%88%A9%E7%94%A8%E6%A6%82%E8%A6%81.pdf)（S2）；[Jグランツ 利用规约](https://www.jgrants-portal.go.jp/stipulation)（S3）。 |
| Key / 认证 / 申请 | S2 写明认证功能与利用申请均无须；Key 是否另有定义未单独说明。（F1；读过官方文档（2026-10-30）；S2；api_overview.txt:17, api_overview.txt:18） |
| 许可 / 利用规约 | 材料包括 Web-API 利用规约（S1）与门户服务规约（S3）；没有为 API 输出明确命名许可。门户规约的 CC BY 条款是否覆盖 API 返回数据仍待确认。（读过官方文档（2026-10-30）；S1；api_terms.txt:4, api_terms.txt:20, api_terms.txt:21；读过官方页面（2026-10-30）；S3；portal_stipulation_excerpt.txt:64） |
| 可否再分发 | 待确认；材料没有说明 API 输出的再分发状态，且 Web-API 与门户规约对 API 主机数据的适用关系未说明。（F6、F7；api_terms.txt:20, api_terms.txt:21；portal_stipulation_excerpt.txt:25, portal_stipulation_excerpt.txt:37, portal_stipulation_excerpt.txt:64；datumoj_issue48_body.md:18） |
| 本资料的可信度 | 已阅读 S1、S2 官方文档及 S3 官方页面文本；本次没有发起实时 API 请求。S4 所述实测属于维护者自述，未由本资料核实。（S1；api_terms.txt:2；S2；api_overview.txt:2；S3；portal_stipulation_excerpt.txt:2；S4；datumoj_issue48_body.md:47, datumoj_issue48_body.md:49, datumoj_issue48_body.md:50, datumoj_issue48_body.md:51） |
| 对应的 DATUMOJ 提案 | [issue 48](https://github.com/gorou5/datumoj/issues/48)；字段逐项对照见[FIELD_UPDATE_TABLE.md](../FIELD_UPDATE_TABLE.md)。（仅读文档；S4；datumoj_issue48_body.md:1, datumoj_issue48_body.md:32, datumoj_issue48_body.md:89） |

## 内容清单

- notes.md：接口概要、证据标签、调用限制、日期及维护者自述的边界。（仅读文档；S1–S4）
- terms.md：Web-API 规约摘要、署名与加工说明、门户规约范围问题及开放事项。（仅读文档；S1–S3）
- 本包只整理所列材料；API 输出的许可、再分发及门户规约适用范围仍待确认。（仅读文档；S1–S3）
