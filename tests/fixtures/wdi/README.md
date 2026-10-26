# wdi：World Bank WDI API v2 离线样本

供 DATUMOJ-MAPLE-303（世界银行 WDI 精选指标 → 观测值） 离线测试使用。

- 录制：2026-10-26（JST），User-Agent `DATUMOJ-fixture-recorder/0.1 (+https://github.com/gorou5/datumoj)`，每秒不超过 1 个请求，不跟随重定向，请求头 `Accept-Encoding: identity`（响应体就是服务器原样返回的字节）。
- 录制前用 `datumoj.core.http.RobotsRules` 按 `DATUMOJ` 判断 robots.txt，被禁止的请求不发出。
- 每个文件的 URL、状态码、响应头（去掉 `Set-Cookie`）、抓取时间、字节数、SHA-256、样本类型都在 `_responses.json`。
- 完整性校验：在本目录运行 `shasum -a 256 -c SHA256SUMS`（覆盖除 README.md 外的全部文件）。
- 样本类型：`captured` = 原样保存；`sanitized` = 脱敏；`derived` = 从原文件中取出（见各文件说明）。
- `https://api.worldbank.org/robots.txt` 返回 404（按 RFC 9309 视为没有限制）。

## 许可与署名

- 四个选定指标的指标页都标 CC BY 4.0；另有 World Bank 数据集附加条款（<https://www.worldbank.org/ext/en/legal/terms-conditions/datasets>）。结论只对这些指标成立。
- 署名格式：「The World Bank: World Development Indicators: <数据提供方>」。`NY.GDP.MKTP.CN` 的数据提供方见 `indicator_NY.GDP.MKTP.CN.json` 的 `sourceOrganization`。

## 文件一览

| 文件名 | 类型 | 来源 URL | 抓取时间（JST） | 字节 | 用途（测试点） |
|---|---|---|---|---|---|
| `gdp_cd_cn_JPN-USA_2020-2024_p1.json` | captured | `https://api.worldbank.org/v2/country/JPN;USA/indicator/NY.GDP.MKTP.CD;NY.GDP.MKTP.CN?source=2&format=json&date=2020:2024&per_page=10&page=1` | 2026-10-26T18:08:04+09:00 | 2171 | 303 分页第 1 页：`pages=2`、`per_page=10`、`total=20`；两个指标（美元 GDP、本币 GDP）× 2 国 × 5 年；`source=2` |
| `gdp_cd_cn_JPN-USA_2020-2024_p2.json` | captured | `https://api.worldbank.org/v2/country/JPN;USA/indicator/NY.GDP.MKTP.CD;NY.GDP.MKTP.CN?source=2&format=json&date=2020:2024&per_page=10&page=2` | 2026-10-26T18:08:05+09:00 | 2167 | 303 分页第 2 页（真实续页），必须据 `pages` 停止 |
| `indicator_NY.GDP.MKTP.CN.json` | captured | `https://api.worldbank.org/v2/indicator/NY.GDP.MKTP.CN?format=json` | 2026-10-26T18:08:06+09:00 | 934 | 303 指标元数据：`unit` 是空字符串，单位只在 `sourceNote` 里（local currency units）；`sourceOrganization` 用于署名 |

## 观察

- 美元 GDP 和本币 GDP 在同一个响应里交错出现，解析时必须按 `indicator.id` 区分单位，不能混在一起。
- 0.4 案例需要 2000–2024 年；样本只取 2020–2024 以控制体积。
