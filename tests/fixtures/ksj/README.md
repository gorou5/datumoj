# ksj：国土数値情報 P05（市町村役場等及び公的集会施設）2022 年版 离线样本

供 DATUMOJ-MAPLE-304（国土数値情報 + 経済センサス：命名文件、ZIP 混合编码） 离线测试使用。

- 录制：2026-10-26（JST），User-Agent `DATUMOJ-fixture-recorder/0.1 (+https://github.com/gorou5/datumoj)`，每秒不超过 1 个请求，不跟随重定向，请求头 `Accept-Encoding: identity`（响应体就是服务器原样返回的字节）。
- 录制前用 `datumoj.core.http.RobotsRules` 按 `DATUMOJ` 判断 robots.txt，被禁止的请求不发出。
- 每个文件的 URL、状态码、响应头（去掉 `Set-Cookie`）、抓取时间、字节数、SHA-256、样本类型都在 `_responses.json`。
- 完整性校验：在本目录运行 `shasum -a 256 -c SHA256SUMS`（覆盖除 README.md 外的全部文件）。
- 样本类型：`captured` = 原样保存；`sanitized` = 脱敏；`derived` = 从原文件中取出（见各文件说明）。
- `https://nlftp.mlit.go.jp/robots.txt` 返回 404（按 RFC 9309 视为没有限制）。

## 许可与署名

- 数据页 <https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-P05-2022.html> 标注「オープンデータ（CC_BY_4.0）」。只对 P05-2022 成立，不推广到整个国土数値情報（总规约 <https://nlftp.mlit.go.jp/ksj/other/agreement.html> 默认 PDL1.0 并有例外）。
- 出处：「国土数値情報（市町村役場等及び公的集会施設データ）（国土交通省）（https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-P05-2022.html）を加工して作成」（加工时）。

## 文件一览

| 文件名 | 类型 | 来源 URL | 抓取时间（JST） | 字节 | 用途（测试点） |
|---|---|---|---|---|---|
| `P05-22_41_GML.zip` | captured | `https://nlftp.mlit.go.jp/ksj/gml/data/P05/P05-22/P05-22_41_GML.zip` | 2026-10-26T18:08:08+09:00 | 69725 | 304 佐賀県（41），原样 ZIP（不重打包）：ZIP 内文件名都是 ASCII；**编码混合**：`KS-META-P05-22_41.xml` 和 `P05-22_41.dbf` 是 cp932，`P05-22_41.geojson` 和 `P05-22_41.xml`（GML）是 UTF-8；另有 `.shp` / `.shx` / `.prj` |

## 观察

- 全国 47 个县里实际体积最小（69,725 字节）；福井县页面上同标 0.07MB，但实测 78,381 字节。
- 数据页只写了 GML 和シェープファイル，实际 ZIP 里还有 GeoJSON。
