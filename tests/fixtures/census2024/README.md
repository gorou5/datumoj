# census2024：令和6年経済センサス‐基礎調査（甲調査）离线样本

供 DATUMOJ-MAPLE-304（国土数値情報 + 経済センサス：命名文件） 离线测试使用。

- 录制：2026-10-26（JST），User-Agent `DATUMOJ-fixture-recorder/0.1 (+https://github.com/gorou5/datumoj)`，每秒不超过 1 个请求，不跟随重定向，请求头 `Accept-Encoding: identity`（响应体就是服务器原样返回的字节）。
- 录制前用 `datumoj.core.http.RobotsRules` 按 `DATUMOJ` 判断 robots.txt，被禁止的请求不发出。
- 每个文件的 URL、状态码、响应头（去掉 `Set-Cookie`）、抓取时间、字节数、SHA-256、样本类型都在 `_responses.json`。
- 完整性校验：在本目录运行 `shasum -a 256 -c SHA256SUMS`（覆盖除 README.md 外的全部文件）。
- 样本类型：`captured` = 原样保存；`sanitized` = 脱敏；`derived` = 从原文件中取出（见各文件说明）。
- `https://www.e-stat.go.jp/robots.txt` 返回 200；`/stat-search/file-download` 未被禁止。下载直接返回 200，没有跳转。

## 许可与署名

- 文件从 e-Stat 下载，适用政府統計の総合窓口（e-Stat）利用規約（<https://www.e-stat.go.jp/terms-of-use>，可按 CC BY 4.0 利用）。
- 出处：「出典：政府統計の総合窓口(e-Stat)（https://www.e-stat.go.jp/）」，并写明「令和6年経済センサス‐基礎調査 甲調査」、表名和 statInfId；加工时另行说明。

## 文件一览

| 文件名 | 类型 | 来源 URL | 抓取时间（JST） | 字节 | 用途（测试点） |
|---|---|---|---|---|---|
| `c1_001_1.xlsx` | captured | `https://www.e-stat.go.jp/stat-search/file-download?statInfId=000040389320&fileKind=0` | 2026-10-26T18:09:21+09:00 | 102198 | 304 表 1-1（statInfId 000040389320，DB 版 sid 0004040098）：全国、都道府県、市区町村别的民営事業所数和従業者数，不分产业；原样 Excel，文件名取自响应头 `Content-Disposition` |

## 观察

- 首版范围原定表 5（产业×都道府県），但它有 7,928,961 字节，超过每个来源 5 MiB 的样本预算，所以首版改用表 1-1，表 5 以后再加。
- 甲调查排除了没有雇员的个人经营事業所，与部分往年调查的对象范围不同。
- 甲调查正式集计 2025-12-24 公开；速報集計（2025-05-30）另放，不混用。
