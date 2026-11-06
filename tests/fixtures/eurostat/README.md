# eurostat：Eurostat SDMX 2.1 离线样本

供 DATUMOJ-MAPLE-302（Eurostat SDMX 精选数据集 → 观测值） 离线测试使用。

- 录制：2026-10-26（JST），User-Agent `DATUMOJ-fixture-recorder/0.1 (+https://github.com/gorou5/datumoj)`，每秒不超过 1 个请求，不跟随重定向，请求头 `Accept-Encoding: identity`（响应体就是服务器原样返回的字节）。
- 录制前用 `datumoj.core.http.RobotsRules` 按 `DATUMOJ` 判断 robots.txt，被禁止的请求不发出。
- 每个文件的 URL、状态码、响应头（去掉 `Set-Cookie`）、抓取时间、字节数、SHA-256、样本类型都在 `_responses.json`。
- 完整性校验：在本目录运行 `shasum -a 256 -c SHA256SUMS`（覆盖除 README.md 外的全部文件）。
- 样本类型：`captured` = 原样保存；`sanitized` = 脱敏；`derived` = 从原文件中取出（见各文件说明）。
- `https://ec.europa.eu/robots.txt` 返回 200；当前 API 路径 `/eurostat/api/dissemination/sdmx/2.1/` 未被禁止（旧 SDMX 路径被禁止，不用）。

## 许可与署名

- Eurostat 版权与再利用政策（<https://ec.europa.eu/eurostat/help/copyright-notice>）：统计数据可用于商业和非商业用途，须注明来源；修改须说明。EU、EFTA、入盟国和候选国以外国家的数据不能商业再利用；本目录只有 EL、DE、FR、NL（EU 成员国）。
- 出处：「Source: Eurostat, <数据集代码>」，并写明查询 key 和抓取时间。

## 文件一览

| 文件名 | 类型 | 来源 URL | 抓取时间（JST） | 字节 | 用途（测试点） |
|---|---|---|---|---|---|
| `gov_10dd_edpt1_EL_2000-2015.xml` | captured | `https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/gov_10dd_edpt1/A.PC_GDP+MIO_EUR.S13.B9+GD.EL?startPeriod=2000&endPeriod=2015` | 2026-10-26T18:07:59+09:00 | 6486 | 302 希腊一般政府赤字和债务：key `A.PC_GDP+MIO_EUR.S13.B9+GD.EL`，2000–2015，4 条序列×16 年＝64 个观测；默认 SDMX-ML 2.1 Generic；`SeriesKey` 里维度的顺序（geo 在前）和 DSD 不同，解析不能按位置 |
| `datastructure_GOV_10DD_EDPT1.xml` | captured | `https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/datastructure/ESTAT/GOV_10DD_EDPT1` | 2026-10-26T18:08:00+09:00 | 6537 | 302 用 DSD 解释维度顺序（freq, unit, sector, na_item, geo）；不含 codelist |
| `nama_10_gdp_DE-FR-NL_2020-2024.xml` | captured | `https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/nama_10_gdp/A.CP_MEUR.B1GQ.DE+FR+NL?startPeriod=2020&endPeriod=2024` | 2026-10-26T18:08:01+09:00 | 2813 | 302 GDP 现价百万欧元：key `A.CP_MEUR.B1GQ.DE+FR+NL`，3 国×5 年＝15 个观测；有 3 个观测带 `OBS_FLAG`，要原样保留 |
| `datastructure_NAMA_10_GDP.xml` | captured | `https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/datastructure/ESTAT/NAMA_10_GDP` | 2026-10-26T18:08:02+09:00 | 5996 | 302 DSD：维度顺序 freq, unit, na_item, geo |

## 观察

- 这些是 2026-10-26 当时的“当前值”，不是历史发布版本。历次发布的值只能靠之后的定期快照积累。
- `format=JSON` 返回 JSON-stat 2.0，不是 SDMX-JSON；首版统一用 SDMX-ML。
- 带 `?references=datastructure` 查 dataflow 返回 400（错误码 140），所以直接查 `/datastructure/ESTAT/<ID>`。
