# Federal Register（us-fedreg）资料笔记

## 证据标签

| 标签 | 页面标题 | 官方页面 URL | 捕获时间 | HTTP 状态 |
|---|---|---|---|---|
| S1 | 1 CFR 2.6 — Unrestricted use | https://www.ecfr.gov/current/title-1/chapter-I/subchapter-A/part-2/section-2.6 | 2026-10-31 17:52 JST | 200 |
| S2 | About This Site | https://www.federalregister.gov/reader-aids/government-policy-and-ofr-procedures/about-this-site | 2026-10-31 17:53 JST | 200 |
| S3 | API Documentation | https://www.federalregister.gov/developers/documentation/api/v1 | 2026-10-31 17:52 JST | 200 |
| S4 | REST API & Interactive Documentation | https://www.federalregister.gov/reader-aids/developer-resources/rest-api | 2026-10-31 17:52 JST | 200 |
| S5 | robots.txt | https://www.federalregister.gov/robots.txt | 2026-10-31 17:52 JST | 200 |

捕获时间与 HTTP 状态取自各文件首行的捕获元数据，不是页面正文。

本文件中的 `文件名:行号` 指向未随本包提交的本地保存记录（公开仓库不提交未核对再分发范围的原文）；读者核对时以各标签对应的官方 URL 为准。

## 1. API 入口与 Key

- 官方说明入口为 S3 的 API Documentation 与 S4 的 REST API 页面。S3 介绍公开 API endpoints；S4 介绍 REST API、交互式文档和端点说明。页面没有给出 API 根路径。
- 维护者侧材料 `source.yaml:5` 将 base_url 设为 `https://www.federalregister.gov/api/v1/`；该值是配置记录，未在捕获页面正文中找到。
- S3、S4 均说明 API 不需要 API keys。〔证据:fedreg_api_developer.txt:93〕〔证据:fedreg_reader_aids_dev.txt:96〕

> 「FederalRegister.gov APIs do not require API keys; all you need is an HTTP client or browser.」〔证据:fedreg_api_developer.txt:93〕

## 2. 返回格式、分页与参数

- S4 说明 API 可返回 CSV 或 JSON；多数请求可自选返回的数据字段。〔证据:fedreg_reader_aids_dev.txt:98〕

> 「The Federal Register.gov API returns data in either CSV or JSON formats.」〔证据:fedreg_reader_aids_dev.txt:98〕

- 页面说明结果分页、每页数量可配置，并称可翻阅的范围限于前 2,000 条；需要更多结果时使用日期范围过滤。〔证据:fedreg_reader_aids_dev.txt:99〕

> 「Results are paginated; users can customize how many results are returned per page.」〔证据:fedreg_reader_aids_dev.txt:99〕
> 「you can only paginate through the first 2000 results.」〔证据:fedreg_reader_aids_dev.txt:99〕
> 「If you wish to download more results, use the date range filters.」〔证据:fedreg_reader_aids_dev.txt:99〕

- S4 描述单文书、多个文书和搜索端点；单文书读取以 Federal Register 文书编号为入口。〔证据:fedreg_reader_aids_dev.txt:106-111〕
- S4 的保存页面没有写明 `per_page` 的最小值或最大值、超过第 50 页时的行为、`total_pages` 上限、`search_after_cursor` 字段或具体日期过滤参数名。以上均为 NOT STATED；本轮没有实际调用 API，故对应实测结论为 NOT CHECKED。
- 本轮输入没有捕获 API JSON 响应，故响应字段与实际分页行为为 NOT CAPTURED。

## 3. robots.txt 与路径

S5 的 `User-Agent: *` 规则列出以下路径限制：

- `/documents/current`。〔证据:fedreg_robots.txt:5〕
- `/documents/email-a-friend`。〔证据:fedreg_robots.txt:6〕
- `/articles/search`、`/documents/search`、`/public-inspection/search`、`/regulations/search`。〔证据:fedreg_robots.txt:8-11〕
- `/my/`、`/auth/`。〔证据:fedreg_robots.txt:13-14〕

捕获的 robots.txt 没有列出维护者配置中的 `/api/v1/` 路径；这里只记录文件内容，不据此推定该路径的使用状态。保存的规则没有给出通用 crawl-delay。〔证据:fedreg_robots.txt:4-14〕

## 4. 上游配置与未述事项

- 维护者侧材料 `source.yaml:23-25` 配置文书类型、起始日期和 lookback 天数；捕获页面没有陈述这些项目配置值。
- 维护者侧材料 `plan_excerpt.md` 中的分页、过滤、文书类型及历史实测陈述均按原出处在 out/PLAN_CHECK_TABLE.md 逐项对照。该侧车是维护者侧材料。
- NOT STATED：捕获页面没有写明维护者所用的文书类型取值、具体 API 根路径、配置起始日期或 lookback 天数。
- NOT CHECKED：本轮没有复现上游维护者记录的 API 实测，也没有查询官方 API。
- NOT CAPTURED：本包不含响应样本；capture_log.jsonl 仅为本轮捕获记录，不是官方页面，也不随包提交。
