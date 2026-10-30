# ndb：第 11 回 NDB オープンデータ（特定健診 質問票）离线样本

供 DATUMOJ-MAPLE-305（医療オープンデータ + ABR 地址） 离线测试使用。

- 录制：2026-10-26（JST），User-Agent `DATUMOJ-fixture-recorder/0.1 (+https://github.com/gorou5/datumoj)`，每秒不超过 1 个请求，不跟随重定向，请求头 `Accept-Encoding: identity`（响应体就是服务器原样返回的字节）。
- 录制前用 `datumoj.core.http.RobotsRules` 按 `DATUMOJ` 判断 robots.txt，被禁止的请求不发出。
- 每个文件的 URL、状态码、响应头（去掉 `Set-Cookie`）、抓取时间、字节数、SHA-256、样本类型都在 `_responses.json`。
- 完整性校验：在本目录运行 `shasum -a 256 -c SHA256SUMS`（覆盖除 README.md 外的全部文件）。
- 样本类型：`captured` = 原样保存；`sanitized` = 脱敏；`derived` = 从原文件中取出（见各文件说明）。
- `https://www.mhlw.go.jp/robots.txt` 返回 200；`/content/` 路径未被禁止。

## 许可与署名

- 厚生労働省网站政策（<https://www.mhlw.go.jp/chosakuken/index.html>）适用公共データ利用規約（第1.0版）（PDL1.0），须注明出处；NDB 页面没有单独的许可。
- 出处：「出典：厚生労働省 第11回NDBオープンデータ（https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/0000177221_00017.html）」，加工时另行说明。

## 文件一览

| 文件名 | 类型 | 来源 URL | 抓取时间（JST） | 字节 | 用途（测试点） |
|---|---|---|---|---|---|
| `ndb11_kenshin_shitsumon01_pref_sex_age.xlsx` | derived | `https://www.mhlw.go.jp/content/12400000/001711941.zip` | 2026-10-26T18:08:13+09:00 | 24262 | 305 从特定健診 質問票 ZIP（3,148,349 字节，`001711941.zip`）取出的单个 xlsx，字节未改（`derived`）：标準的な質問票（質問項目１）都道府県別性年齢階級別分布；令和 5 年度健診分；小格子遮蔽为半角 `-` |

## 观察

- 原 ZIP 里的文件名是 cp932 编码，且没有设 UTF-8 标志（共 45 个条目）；ZIP 内路径原文见 `_responses.json` 的 `extracted_from.member`。采集时只能整包下载约 3MB 的 ZIP 再取出文件。
- 遮蔽规则（解说编第 1-5 节）：集计单位小于 10 时显示 `-`；某组只有一个值被遮时，为防反推，10 以上的最小值也会被遮；「0 不遮」的例外只适用于レセプト。采集层原样保留，不改成 0。
- 第 11 回于 2026-06-16 公开；特定健診是令和 5 年度（2023 年度实施分）。
