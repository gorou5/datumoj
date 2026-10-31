# data.gouv.fr（INSEE 组织）（fr-data-gouv）

| 项目 | 内容 |
|---|---|
| 提供者 | 37（jp-gov-data-api）团队 |
| 资料整理日期 | 2026-10-31 |
| 资料核对日期 | 2026-10-31 |
| 官方入口 | <https://www.data.gouv.fr/api/1/>；API 指南：<https://guides.data.gouv.fr/api-de-data.gouv.fr/prise-en-main>。 |
| 需要 Key | 写操作需要 Key；所读页面没有说明只读请求是否需要 Key。〔证据:S1〕〔证据:datagouv_api_prise_en_main.txt:75-83〕 |
| 许可 / 利用规约 | Licence Ouverte 2.0（https://www.data.gouv.fr/pages/legal/licences/etalab-2.0）；Modalités d’utilisation（https://www.data.gouv.fr/pages/legal/cgu）；Licences（https://www.data.gouv.fr/pages/legal/licences/）；Mentions légales（https://www.data.gouv.fr/pages/legal/legal-notice）。可否再分发：未核对。所读页面中的利用规约文字见维护者侧材料 datagouv_licence_etalab2.txt:20-39、datagouv_cgu.txt:70、datagouv_legal_notice.txt:78；本包只记录页面文字，结论由维护者的 H2 核验决定。 |
| 本资料的可信度 | 仅读文档：S1-S6；已用真实请求验证：S7 JSON 响应与 S8 robots.txt 的本次响应形状；推测：无。 |
| 对应的 DATUMOJ 任务 | DATUMOJ-MAPLE-402 |

## 内容清单

- notes.md：API 根入口、Key 说明、通用分页样例、robots 路径与未说明事项。
- terms.md：许可与利用规约摘要、署名措辞、排除事项及未决点。
- 本包不提交捕获页、API 响应或样本目录。
