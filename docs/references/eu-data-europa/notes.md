# data.europa.eu（Publications Office 目录 publ）资料笔记

## 证据标签

| 标签 | 页面标题或记录 | 官方 URL | 捕获时间 | HTTP 状态 |
|---|---|---|---|---|
| S1 | API documentation - Documentation of the European Data Portal (EDP) | https://dataeuropa.gitlab.io/data-provider-manual/api-documentation/ | 2026-10-31 17:54 JST | 200 |
| S2 | catalogue publ 记录；dct:title 为 Publications Office | https://data.europa.eu/api/hub/repo/catalogues/publ | 2026-10-31 17:52 JST | 200 |
| S3 | hub-repo - OpenAPI Description | https://data.europa.eu/api/hub/repo/ | 2026-10-31 17:53 JST | 200 |
| S4 | Legal notice | https://data.europa.eu/en/legal-notice | 2026-10-31 17:52 JST | 200 |
| S5 | robots.txt | https://data.europa.eu/robots.txt | 2026-10-31 17:53 JST | 200 |

本文件中的 `文件名:行号` 指向未随本包提交的本地保存记录（公开仓库不提交未核对再分发范围的原文）；读者核对时以各标签对应的官方 URL 为准。

S 表中的 URL、捕获时间和 HTTP 状态取自本地保存记录的捕获头，仅作捕获元数据，不作为页面正文证据。

## 1. Registry API 与 catalogue publ

- API 说明列出 Search、SPARQL、Registry 等读取元数据的 API；Registry 根入口为 https://data.europa.eu/api/hub/repo/。〔证据:S1；dataeuropa_api_documentation.txt:95-101〕〔证据:S1；dataeuropa_api_documentation.txt:185-193〕
- Registry 文档示例列出标识符查询 GET https://data.europa.eu/api/hub/repo/datasets?limit=50&valueType=identifiers；示例中的 limit 为 50。该例是 Registry 通用列表路径，不是 catalogue publ 专用路径。〔证据:S1；dataeuropa_api_documentation.txt:185-189〕
- 文档以单个数据集标识符请求详情；默认返回 JSON-LD，可在路径末尾加 .ttl 请求 Turtle 表示。〔证据:S1；dataeuropa_api_documentation.txt:189-193〕
- S2 保存的是 catalogue publ 记录：记录标识为 http://data.europa.eu/88u/catalogue/publ，dct:title 为 Publications Office，类型字段为 dcat:Catalog。该记录没有列出目录内的数据集数量。〔证据:S2；dataeuropa_catalogue_publ.txt:2-3〕〔证据:S2；dataeuropa_catalogue_publ.txt:188-190〕〔证据:S2；dataeuropa_catalogue_publ.txt:238-244〕
- 该记录含 issued、modified 和 transReceived 时间值；这些是本条记录字段，不说明全目录的更新安排。〔证据:S2；dataeuropa_catalogue_publ.txt:208-218〕

## 2. Key 与访问要求

- 已保存的 Registry 读取示例是 GET；这些示例未显示 Key 字段。页面没有明说读取端是否需要 Key，因此此项为未核对。〔证据:S1；dataeuropa_api_documentation.txt:185-193〕
- 数据集管理章节说明使用 OpenID Connect 做访问控制，并称管理数据集需要对相应 catalogue 有写入权限。示例写入请求包含 an authorization header；本包不记录任何凭证值。〔证据:S1；dataeuropa_api_documentation.txt:102-109〕〔证据:S1；dataeuropa_api_documentation.txt:123-135〕

## 3. 分页与参数

- Registry 列表示例使用 valueType=identifiers 与 limit=50；所读文档未说明 Registry 的最大 limit、offset 推进规则或排序稳定性。〔证据:S1；dataeuropa_api_documentation.txt:185-193〕
- Search API 的示例使用 page 与 limit，并说明 limit 是每页结果上限；示例值包括 10 和 100。该分页说明针对 Search API。〔证据:S1；dataeuropa_api_documentation.txt:195-204〕
- 所读页面没有给出 catalogue publ 专用的数据集列表请求示例，也没有给出该 catalogue 的条目数量。〔证据:S1；dataeuropa_api_documentation.txt:185-193〕〔证据:S2；dataeuropa_catalogue_publ.txt:2-3〕
- 所读页面没有说明适用于读取 API 的统一速率数字。〔证据:S1；dataeuropa_api_documentation.txt:94-101〕〔证据:S3；dataeuropa_repo_root.txt:2-4〕

## 4. robots.txt 与源路径

- S5 中 User-agent: * 段包含 Disallow: /*search?* 与 Disallow: /*&sort=*。〔证据:S5；dataeuropa_robots.txt:17-18〕〔证据:S5；dataeuropa_robots.txt:64-72〕
- API 文档展示的 Search URL 含有 /search/search?... 形状；该路径与上列 search 规则相符。此处是对保存文本的路径比对，没有运行单独的 RobotsRules 解析。〔证据:S1；dataeuropa_api_documentation.txt:194-204〕〔证据:S5；dataeuropa_robots.txt:67-68〕
- source.yaml 的 Registry base_url 是 /api/hub/repo/；S5 所列规则中没有显式的 /api/hub/repo/ 条目。若查询 URL 带有 &sort=，仍应注意上列参数规则；本记录不把未列规则解释成许可结论。〔证据:S3；dataeuropa_repo_root.txt:2-4〕〔证据:S5；dataeuropa_robots.txt:64-72〕
- robots 文件另对 Googlebot、Bingbot 列有路径规则；这些是各自 User-agent 段中的文字。〔证据:S5；dataeuropa_robots.txt:79-87〕

## 5. 页面未说明或本包未捕获

- 读取 API 是否需要 Key：NOT STATED。
- Registry 的最大 limit、offset 规则、排序稳定性、统一读取速率：NOT STATED。
- catalogue publ 的数据集数及其 catalogue 专用清单响应：NOT STATED / NOT CAPTURED。
- 当前持有的 PUBL JSON-LD 是 catalogue 记录，不是数据集详情或目录清单样本；本包没有 samples/。〔证据:S2；dataeuropa_catalogue_publ.txt:2-3〕
- 法律声明称资源页面通常标出各自条款；缺少条款标示时，页面指向资源最初发布的门户。单个资源的条款不由本记录推定。〔证据:S4；dataeuropa_legal_notice.txt:199-200〕
