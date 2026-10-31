# data.europa.eu（Publications Office 目录 publ）（eu-data-europa）

| 项目 | 内容 |
|---|---|
| 提供者 | 37（jp-gov-data-api）团队 |
| 资料整理日期 | 2026-10-31 |
| 资料核对日期 | 2026-10-31 |
| 官方入口 | [Registry API](https://data.europa.eu/api/hub/repo/)；[catalogue publ record](https://data.europa.eu/api/hub/repo/catalogues/publ)；[API documentation](https://dataeuropa.gitlab.io/data-provider-manual/api-documentation/)。〔证据:dataeuropa_api_documentation.txt:95-101〕〔证据:dataeuropa_repo_root.txt:2〕 |
| 需要 Key | 未核对；Registry GET 示例未呈现 Key 字段，文档未明示读取接口要求。〔证据:dataeuropa_api_documentation.txt:185-193〕 |
| 许可 / 利用规约 | Creative Commons CC0 1.0 Universal Public Domain Dedication deed；网站编辑内容另列 CC BY 4.0；官方页面：https://data.europa.eu/en/legal-notice。可否再分发：未核对。该页的编辑内容与门户元数据规约文字见维护者侧材料 dataeuropa_legal_notice.txt:195-200；本包记录页面文字，决定留待维护者 H2 核对。 |
| 本资料的可信度 | 仅读文档：API 说明、Registry 说明与法律声明（S1、S3、S4）；已用真实请求验证：PUBL catalogue JSON-LD 记录与 robots.txt 响应的形状（S2、S5）。两项只说明本次响应形状；推测：无。〔证据:dataeuropa_catalogue_publ.txt:2-3〕〔证据:dataeuropa_robots.txt:17-18〕 |
| 对应的 DATUMOJ 任务 | DATUMOJ-MAPLE-403 |

## 内容清单

- notes.md：Registry API、PUBL catalogue 记录、读取与写入说明、分页边界、robots 规则及未说明事项。
- terms.md：法律声明中编辑内容、门户元数据、第三方权利、个人数据与免责声明的摘要及短引文。
- 本包不附真实响应样本；未建立 samples/。
