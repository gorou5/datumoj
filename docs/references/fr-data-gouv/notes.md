# data.gouv.fr（INSEE 组织）（fr-data-gouv）资料笔记

## 证据标签

| 标签 | 页面标题 | 官方 URL | 取得时间 | HTTP 状态 |
|---|---|---|---|---|
| S1 | Prise en main | https://guides.data.gouv.fr/api-de-data.gouv.fr/prise-en-main | 2026-10-31 17:54 JST | 200；标题〔证据:datagouv_api_prise_en_main.txt:2〕 |
| S2 | Référence | https://guides.data.gouv.fr/api-de-data.gouv.fr/reference | 2026-10-31 17:54 JST | 200；标题〔证据:datagouv_api_reference_guide.txt:2〕 |
| S3 | Modalités d’utilisation | https://www.data.gouv.fr/pages/legal/cgu | 2026-10-31 17:52 JST | 200；标题〔证据:datagouv_cgu.txt:2〕 |
| S4 | Mentions légales | https://www.data.gouv.fr/pages/legal/legal-notice | 2026-10-31 17:54 JST | 200；标题〔证据:datagouv_legal_notice.txt:2〕 |
| S5 | Licences | https://www.data.gouv.fr/pages/legal/licences/ | 2026-10-31 17:52 JST | 200；标题〔证据:datagouv_licences.txt:2〕 |
| S6 | Licence Ouverte 2.0 | https://www.data.gouv.fr/pages/legal/licences/etalab-2.0 | 2026-10-31 17:52 JST | 200；标题〔证据:datagouv_licence_etalab2.txt:2〕 |
| S7 | Organization API JSON response (Insee) | https://www.data.gouv.fr/api/1/organizations/534fff81a3a7292c64a77e5c/ | 2026-10-31 17:52 JST | 200；名称字段〔证据:datagouv_org_534fff81.json.txt:4〕 |
| S8 | robots.txt | https://www.data.gouv.fr/robots.txt | 2026-10-31 17:52 JST | 200；规则正文〔证据:datagouv_robots.txt:2〕 |

本文件中的 `文件名:行号` 指向未随本包提交的本地保存记录（公开仓库不提交未核对再分发范围的原文）；读者核对时以各标签对应的官方 URL 为准。

表中 URL、取得时间和 HTTP 状态均来自对应捕获文件首行元数据；首行仅用于这三项，不作为页面文字引用。S6 捕获头同时记录请求 URL 与最终 URL；最终 URL 为 https://www.data.gouv.fr/pages/legal/licences/etalab-2-0。

## 1. API 入口与 Key

- S1 将 API 根入口列为 https://www.data.gouv.fr/api/1/。〔证据:S1〕〔证据:datagouv_api_prise_en_main.txt:78〕

> 「Le point d’entrée racine de l’API est https://www.data.gouv.fr/api/1/.」〔证据:S1〕〔证据:datagouv_api_prise_en_main.txt:78〕

- S1 说明写操作需要 API Key，并给出写请求头；页面没有说明只读 GET 是否需要 Key。〔证据:S1〕〔证据:datagouv_api_prise_en_main.txt:75-83〕

> 「Cette clé doit être fournie dans l’entête HTTP X-API-KEY à chaque appel en écriture」〔证据:S1〕〔证据:datagouv_api_prise_en_main.txt:83〕

- S2 的参考页列出 `/datasets` 与 `/organizations` 路由名称；捕获文本没有为本来源列出 `organization` 查询参数或其错误行为。〔证据:S2〕〔证据:datagouv_api_reference_guide.txt:80-90〕

## 2. 组织响应与分页

- S7 是对组织 API URL 的一次 JSON 响应；捕获记录保留了 `id`、`name`、`slug`、`uri`、`page` 和 `metrics` 等字段形状。该记录只描述这一条响应的字段形状；本包不提交响应样本。〔证据:S7〕〔证据:datagouv_org_534fff81.json.txt:2-11〕
- 组织响应列出的组织 ID 与入口 URL 中的标识一致；返回字段还给出以 slug 组成的组织 `uri`。〔证据:S7〕〔证据:datagouv_org_534fff81.json.txt:3-9〕
- S1 描述分页响应包含 `previous_page` 与 `next_page` 链接；其示例响应列出 `page`、`page_size`、`total` 字段。〔证据:S1〕〔证据:datagouv_api_prise_en_main.txt:103-104〕〔证据:datagouv_api_prise_en_main.txt:150-156〕

> 「dans les attributs previous_page et next_page」〔证据:S1〕〔证据:datagouv_api_prise_en_main.txt:104〕

- 通用响应样例中的 `page_size` 值为 20；页面没有说明最大值、默认值，或请求参数 `page_size` 的范围。〔证据:S1〕〔证据:datagouv_api_prise_en_main.txt:150-156〕
- 页面没有说明针对该组织 ID 的 `/datasets/?organization=...` 查询行为、24 位 ID 与 slug 在查询参数中的差异，或该来源的页数上限。S1 的 URL 标识说明仅讨论 API URL 中的对象标识。〔证据:S1〕〔证据:datagouv_api_prise_en_main.txt:92-98〕

> 「Il est donc préférable d’utiliser les identifiants techniques dans les scripts」〔证据:S1〕〔证据:datagouv_api_prise_en_main.txt:98〕

## 3. robots.txt 与来源路径

- S8 的 `User-agent: *` 规则包括管理、搜索、CSV、预览、重定向资源及 `/api/1/datasets/r/` 等路径。下列引文只记录捕获规则文字。〔证据:S8〕〔证据:datagouv_robots.txt:2-3〕〔证据:datagouv_robots.txt:7-18〕〔证据:datagouv_robots.txt:23-74〕

> 「Disallow: /api/1/datasets/r/」〔证据:S8〕〔证据:datagouv_robots.txt:73〕

- 文件另列 `ChatGPT-User` 与 `GPTBot`，并对特定组织目录 CSV 路径列出规则；这段仅记录文件内容，不把它外推为其他客户端的抓取结论。〔证据:S8〕〔证据:datagouv_robots.txt:76-84〕

> 「Disallow: /api/1/organizations/*/datasets*.csv」〔证据:S8〕〔证据:datagouv_robots.txt:79〕

- `source.yaml` 的 API 根路径是 `/api/1/`，当前组织 URL 是 `/api/1/organizations/<id>/`。捕获文本明确列出的组织 CSV 规则是另一条带 `datasets*.csv` 的路径；本页没有给出实际采集程序的 `RobotsRules` 判定记录。〔证据:S7〕〔证据:datagouv_org_534fff81.json.txt:3-9〕〔证据:S8〕〔证据:datagouv_robots.txt:79-80〕
- S8 在 `User-agent: *` 下列出的 `/organizations?*` 是无 `/api/1/` 前缀的网页路径；文本没有说明它与组织 JSON API 路径的匹配关系。〔证据:S8〕〔证据:datagouv_robots.txt:19-22〕

## 4. 页面未说明的事项

- API 指南只说明写操作的 Key 要求；只读请求的认证要求未说明。〔证据:S1〕〔证据:datagouv_api_prise_en_main.txt:75-83〕
- 捕获页面没有说明 `organization=` 过滤的接受值、查询分页的默认或最大 `page_size`、该来源目录的固定总数或历史计数日期。〔证据:S1〕〔证据:datagouv_api_prise_en_main.txt:92-104〕〔证据:S2〕〔证据:datagouv_api_reference_guide.txt:80-100〕
- 捕获的 API 示例与组织 JSON 不能确认数据集级许可值、资源级许可、具体数据集文件的处理方式，或下载主机的 robots 规则。〔证据:S1〕〔证据:datagouv_api_prise_en_main.txt:66-76〕〔证据:S7〕
