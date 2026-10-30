# 子育て支援制度レジストリ（jp-digital-childcare-registry）

| 项目 | 内容 |
|---|---|
| 提供者 | デジタル庁。S3 的电子申请等 API 页面列出本注册库的两个 API；S4 说明自治体登记制度信息、应用服务商经 API 取得信息。（读过官方页面（2026-10-30）；S3、S4）inputs/held/eshinsei_page.txt:38；inputs/held/eshinsei_page.txt:39；inputs/held/registry_service_page.txt:19；inputs/held/registry_service_page.txt:20 |
| 资料整理日期 | 2026-10-30（本次整理）。（仅读文档；S1–S5） |
| 资料核对日期 | 2026-10-30（核对所持 S1–S4 官方材料与 S5 提案文本）。（读过官方文档（2026-10-30）；S1、S2；读过官方页面（2026-10-30）；S3、S4；仅读文档；S5） |
| 官方入口 | [電子申請等API](https://developers.digital.go.jp/documents/mynaportal-api/specification/eshinsei/)（S3）；[子育て支援制度レジストリ](https://services.digital.go.jp/childrearing-support-registry/)（S4）；[マイナポータルAPI 利用規約 PDF](https://files.microcms-assets.io/assets/7c793323a46a46b7bb9a2ac7d0023301/535e70eb7c5b4918b5ae4b6b202c74c4/mynaportal-api-terms.pdf)（S1）；[機密保持誓約書 PDF](https://files.microcms-assets.io/assets/7c793323a46a46b7bb9a2ac7d0023301/f94b4980b0074de99b70a1480adb85d3/mynaportal-api-commitment.pdf)（S2）。（S3、S4、S1、S2）inputs/held/eshinsei_page.txt:1；inputs/held/registry_service_page.txt:1；inputs/held/myna_terms.txt:1；inputs/held/myna_commitment.txt:1 |
| 认证 / 申请 | S3 列出民間サービス事業者認証API；使用前须阅读利用规约和誓约书，经 API 利用申请表申请并获审查批准。首次使用申请表须先注册账户。规格书另需申请。S3 没有写明 API key。（读过官方页面（2026-10-30）；S3）inputs/held/eshinsei_page.txt:27；inputs/held/eshinsei_page.txt:63；inputs/held/eshinsei_page.txt:64；inputs/held/eshinsei_page.txt:136 S3 also names 第三者開示承諾願 for sharing endpoint information with a development vendor or other third party (inputs/held/eshinsei_page.txt:65). |
| 许可 / 利用规约 | 所持文件为マイナポータルAPI 利用規約（2.3版）与機密保持誓約書。它们不是注册库数据的具名许可；所持文本未说明注册库数据适用何种数据许可。（读过官方文档（2026-10-30）；S1、S2；读过官方页面（2026-10-30）；S3、S4）inputs/held/myna_terms.txt:90；inputs/held/myna_terms.txt:28；inputs/held/myna_terms.txt:30；inputs/held/myna_commitment.txt:9；inputs/held/eshinsei_page.txt:63；inputs/held/registry_service_page.txt:2 |
| 可否再分发 | 待确认。利用规约对 API 及相关程序或著作物列出处理限制；这些条文是否涵盖 API 返回的注册库数据，所持文本未说明。（读过官方文档（2026-10-30）；S1；读过官方页面（2026-10-30）；S3）inputs/held/myna_terms.txt:356；inputs/held/myna_terms.txt:361；inputs/held/eshinsei_page.txt:38 |
| 本资料的可信度 | 已读所持官方 PDF 文本与官方页面文本；S5 是维护者提案，不是官方来源。未发起 live API 请求，也未持有 API 规格书；S3 说明取得规格书需要另行申请。（读过官方文档（2026-10-30）；S1、S2；读过官方页面（2026-10-30）；S3、S4；仅读文档；S5）inputs/held/eshinsei_page.txt:136；inputs/held/eshinsei_page.txt:137 |
| 对应的 DATUMOJ 任务 | [Issue 49 提案](https://github.com/gorou5/datumoj/issues/49) 是维护者提案文本；逐字段对照见 FIELD_UPDATE_TABLE.md。（仅读文档；S5）inputs/held/datumoj_issue49_body.md:1 |

## 内容清单

- notes.md：证据标签、注册库 API 描述、申请流程参考期间与提案数字对照、材料日期。
- terms.md：申请条件、利用规约、知的财产权条款、机密保持誓约与开放事项。
- FIELD_UPDATE_TABLE.md：Issue 49 关于许可、访问、限制及更新字段的逐项对照。
- 本包仅整理所持文本；许可、数据再分发及其他未决事项没有在本包中作出确定。（S1–S5）
