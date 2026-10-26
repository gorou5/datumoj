# data_europa：data.europa.eu（repo 接口）离线样本

供 DATUMOJ-MAPLE-403（data.europa.eu 适配器） 离线测试使用。

- 录制：2026-10-26（JST），User-Agent `DATUMOJ-fixture-recorder/0.1 (+https://github.com/gorou5/datumoj)`，每秒不超过 1 个请求，不跟随重定向，请求头 `Accept-Encoding: identity`（响应体就是服务器原样返回的字节）。
- 录制前用 `datumoj.core.http.RobotsRules` 按 `DATUMOJ` 判断 robots.txt，被禁止的请求不发出。
- 每个文件的 URL、状态码、响应头（去掉 `Set-Cookie`）、抓取时间、字节数、SHA-256、样本类型都在 `_responses.json`。
- 完整性校验：在本目录运行 `shasum -a 256 -c SHA256SUMS`（覆盖除 README.md 外的全部文件）。
- 样本类型：`captured` = 原样保存；`sanitized` = 脱敏；`derived` = 从原文件中取出（见各文件说明）。
- `https://data.europa.eu/robots.txt` 返回 200。search 接口 `/api/hub/search/search?...` 命中 `Disallow: /*search?*`，带 `&sort=` 的 URL 命中 `Disallow: /*&sort=*`；本目录只用 `/api/hub/repo/...`，不命中任何规则。

## 许可与署名

- 门户元数据 CC0 1.0（<https://data.europa.eu/en/legal-notice>）；资源本体的许可各自独立，本目录没有下载任何资源文件。
- 出处：「data.europa.eu」，并保留门户记录 ID、原始数据集 URI 和来源目录 ID。
- 联系方式只有机构邮箱（`op-info-helpdesk@publications.europa.eu`）。

## 文件一览

| 文件名 | 类型 | 来源 URL | 抓取时间（JST） | 字节 | 用途（测试点） |
|---|---|---|---|---|---|
| `repo_catalogue-publ_identifiers_limit5_offset0.json` | captured | `https://data.europa.eu/api/hub/repo/catalogues/publ/datasets?valueType=identifiers&limit=5&offset=0` | 2026-10-26T18:08:23+09:00 | 168 | 403 按目录列 ID：catalogue `publ`（Publications Office），`limit=5&offset=0`，返回 5 个字符串 ID 的数组（没有总数字段，翻页靠返回条数） |
| `repo_dataset_combined-nomenclature-2023.jsonld` | captured | `https://data.europa.eu/api/hub/repo/datasets/combined-nomenclature-2023` | 2026-10-26T18:09:19+09:00 | 98485 | 403 单个数据集详情（JSON-LD，DCAT-AP）：ID 与列表中的 `combined-nomenclature-2023` 对应 |

## 观察

- 选 `publ` 而不选 `estat`：`estat` 目录与 `eu-eurostat` 来源重复，且有 9,936 个数据集，逐个取详情会超过 `max_files`。
