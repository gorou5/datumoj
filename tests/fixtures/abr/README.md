# abr：アドレス・ベース・レジストリ 町字マスター（市区町村）离线样本

供 DATUMOJ-MAPLE-305（医療オープンデータ + ABR 地址） 离线测试使用。

- 录制：2026-10-26（JST），User-Agent `DATUMOJ-fixture-recorder/0.1 (+https://github.com/gorou5/datumoj)`，每秒不超过 1 个请求，不跟随重定向，请求头 `Accept-Encoding: identity`（响应体就是服务器原样返回的字节）。
- 录制前用 `datumoj.core.http.RobotsRules` 按 `DATUMOJ` 判断 robots.txt，被禁止的请求不发出。
- 每个文件的 URL、状态码、响应头（去掉 `Set-Cookie`）、抓取时间、字节数、SHA-256、样本类型都在 `_responses.json`。
- 完整性校验：在本目录运行 `shasum -a 256 -c SHA256SUMS`（覆盖除 README.md 外的全部文件）。
- 样本类型：`captured` = 原样保存；`sanitized` = 脱敏；`derived` = 从原文件中取出（见各文件说明）。
- `https://data.address-br.digital.go.jp/robots.txt` 返回 404（按 RFC 9309 视为没有限制）。文件在 `data.address-br.digital.go.jp`（AmazonS3），目录站点是 `dataset.address-br.digital.go.jp`。

## 许可与署名

- 条目元数据的 licenseInfo 为 CC BY 4.0，accessInformation 指向 ABR 利用規約（<https://www.digital.go.jp/policies/base_registry_address_tos>）。v1 计划文档记为默认 PDL1.0，两者尚未核对一致（见计划文档 8.2），在核对完之前按 `internal_only` 处理。
- 出处：「アドレス・ベース・レジストリ（デジタル庁）」，加工时另行说明。
- 内容是町字名称、读音、代码和日期，没有自然人的个人信息。

## 文件一览

| 文件名 | 类型 | 来源 URL | 抓取时间（JST） | 字节 | 用途（测试点） |
|---|---|---|---|---|---|
| `mt_town_city134023.csv.zip` | captured | `https://data.address-br.digital.go.jp/mt_town/city/mt_town_city134023.csv.zip` | 2026-10-26T18:08:15+09:00 | 561 | 305 東京都青ヶ島村（全国地方公共団体コード 134023），原样 ZIP：内含 `mt_town_city134023.csv`（1,115 字节，UTF-8，无 BOM，38 列）；`lg_code`、`machiaza_id`（如 `0003000`）的前导零要保留；大量空字段；`efct_date` 是 `YYYY-MM-DD` |

## 观察

- 文件最终更新 2026-03-10。另有一条旧条目指向 `gov-csv-export-public.s3.ap-northeast-1.amazonaws.com`（2025-11 版），不用。
