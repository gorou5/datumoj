# fedreg：Federal Register API v1 离线样本

供 DATUMOJ-MAPLE-201（Federal Register 采集器与解析） 离线测试使用。

- 录制：2026-10-26（JST），User-Agent `DATUMOJ-fixture-recorder/0.1 (+https://github.com/gorou5/datumoj)`，每秒不超过 1 个请求，不跟随重定向，请求头 `Accept-Encoding: identity`（响应体就是服务器原样返回的字节）。
- 录制前用 `datumoj.core.http.RobotsRules` 按 `DATUMOJ` 判断 robots.txt，被禁止的请求不发出。
- 每个文件的 URL、状态码、响应头（去掉 `Set-Cookie`）、抓取时间、字节数、SHA-256、样本类型都在 `_responses.json`。
- 完整性校验：在本目录运行 `shasum -a 256 -c SHA256SUMS`（覆盖除 README.md 外的全部文件）。
- 样本类型：`captured` = 原样保存；`sanitized` = 脱敏；`derived` = 从原文件中取出（见各文件说明）。
- `https://www.federalregister.gov/robots.txt` 返回 200；所用的 `/api/v1/` 和 `/documents/full_text/xml/` 路径未被禁止。只用 API，不把网页搜索作后备路径（网页搜索路径被 robots 禁止）。

## 许可与署名

- 依据 1 CFR § 2.6（<https://www.ecfr.gov/current/title-1/chapter-I/subchapter-A/part-2/section-2.6>）：Federal Register 刊载的材料可以复制、再发布。不使用 NARA/OFR 的官方标志或印章。
- 出处记录文书名称、编号、发布日期和来源链接（见 `_responses.json`）。
- **个人信息**：`full_text_2026-20122.xml` 的 FOR FURTHER INFORMATION CONTACT 一节含一名官员的姓名、电话和邮箱，已替换为 `[REDACTED NAME]`、`[REDACTED PHONE]`、`redacted@example.invalid`，其余字节不变；原文件的字节数和 SHA-256 记在 `_responses.json` 的 `original_bytes` / `original_sha256`。

## 文件一览

| 文件名 | 类型 | 来源 URL | 抓取时间（JST） | 字节 | 用途（测试点） |
|---|---|---|---|---|---|
| `documents_rule_gte2026-10-01_p1.json` | captured | `https://www.federalregister.gov/api/v1/documents.json?per_page=20&page=1&order=oldest&conditions%5Bpublication_date%5D%5Bgte%5D=2026-10-01&conditions%5Btype%5D%5B%5D=RULE` | 2026-10-26T18:07:57+09:00 | 39495 | 201 检索第 1 页：`count=187`、`total_pages=10`、`next_page_url`；过滤 `type=RULE`、`publication_date>=2026-10-01`（增量起点），`order=oldest`；默认字段里没有全文链接 |
| `document_2026-20122.json` | captured | `https://www.federalregister.gov/api/v1/documents/2026-20122.json` | 2026-10-26T18:09:15+09:00 | 4762 | 201 单篇详情：`full_text_xml_url`、`body_html_url`、`raw_text_url`、`citation`、`cfr_references`、`docket_ids` |
| `full_text_2026-20122.xml` | sanitized | `https://www.federalregister.gov/documents/full_text/xml/2026/10/01/2026-20122.xml` | 2026-10-26T18:09:38+09:00 | 15087 | 201 全文按节切块：`<RULE>` 下 `PREAMB` / `SUPLINF` / `REGTEXT`，19 个 `<HD>` 小标题（`SOURCE=HED/HD1/HD2`），`<PRTPAGE>` 页码嵌在段落中间 |

## 观察

- API 分页上限（2026-10-26 实测）：`count` 最多显示 10,000、`total_pages` 最多 50；`page` 超过 50 不报错而是静默返回第 1 页，所以必须按日期切窗口。
- `per_page` 最小有效值是 20。
