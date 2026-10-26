# udata_fr：data.gouv.fr（udata API v1）离线样本

供 DATUMOJ-MAPLE-402（data.gouv.fr 适配器） 离线测试使用。

- 录制：2026-10-26（JST），User-Agent `DATUMOJ-fixture-recorder/0.1 (+https://github.com/gorou5/datumoj)`，每秒不超过 1 个请求，不跟随重定向，请求头 `Accept-Encoding: identity`（响应体就是服务器原样返回的字节）。
- 录制前用 `datumoj.core.http.RobotsRules` 按 `DATUMOJ` 判断 robots.txt，被禁止的请求不发出。
- 每个文件的 URL、状态码、响应头（去掉 `Set-Cookie`）、抓取时间、字节数、SHA-256、样本类型都在 `_responses.json`。
- 完整性校验：在本目录运行 `shasum -a 256 -c SHA256SUMS`（覆盖除 README.md 外的全部文件）。
- 样本类型：`captured` = 原样保存；`sanitized` = 脱敏；`derived` = 从原文件中取出（见各文件说明）。
- `https://www.data.gouv.fr/robots.txt` 返回 200；`/api/1/datasets/` 未被禁止。

## 许可与署名

- 门户目录元数据：官方完整目录导出采用 Licence Ouverte 2.0（<https://www.data.gouv.fr/pages/legal/licences/etalab-2.0>）。资源文件的许可按数据集各自的 `license` 字段（这里是 `lov2`），本目录没有下载任何资源文件。
- 出处：「data.gouv.fr」，并保留数据集的最后更新时间（不能用抓取时间代替）。
- 联系方式字段只有机构邮箱（`support-apim@insee.fr`），`owner` 为空，没有自然人信息。

## 文件一览

| 文件名 | 类型 | 来源 URL | 抓取时间（JST） | 字节 | 用途（测试点） |
|---|---|---|---|---|---|
| `datasets_org-insee_page1_size2.json` | captured | `https://www.data.gouv.fr/api/1/datasets/?organization=534fff81a3a7292c64a77e5c&page_size=2&page=1` | 2026-10-26T18:08:20+09:00 | 12756 | 402 INSEE（organization id `534fff81a3a7292c64a77e5c`）列表第 1 页：`total=190`、`page=1`、`page_size=2`、`next_page`；`organization=` 只接受 24 位 id |
| `datasets_org-insee_page2_size2.json` | captured | `https://www.data.gouv.fr/api/1/datasets/?organization=534fff81a3a7292c64a77e5c&page_size=2&page=2` | 2026-10-26T18:08:21+09:00 | 12857 | 402 列表第 2 页（真实续页） |
| `dataset_6ad16a7506eea90157a47a35.json` | captured | `https://www.data.gouv.fr/api/1/datasets/6ad16a7506eea90157a47a35/` | 2026-10-26T18:09:17+09:00 | 6189 | 402 第 1 页第一条的详情：与列表同一永久技术 ID；`resources[]`、`contact_points[]`、`license` 等嵌套结构 |
