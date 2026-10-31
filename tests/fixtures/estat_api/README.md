# estat_api：e-Stat API 3.0（JSON）离线样本

供 DATUMOJ-MAPLE-301（`jp-estat-api`）离线测试使用。

- 录制：2026-11-01（JST），User-Agent `DATUMOJ-fixture-recorder/0.1 (+https://github.com/gorou5/datumoj)`，每 1.2 秒不超过 1 个请求，不跟随重定向，请求头 `Accept-Encoding: identity`（响应体就是服务器原样返回的字节）。
- 录制前用 `datumoj.core.http.RobotsRules` 按 `DATUMOJ` 判断 `https://api.e-stat.go.jp/robots.txt`，被禁止的请求不发出。
- **appId 不在任何文件里。**`_responses.json` 的 URL 一律写成 `appId=<APP_ID>`；录制脚本会把响应体和响应头里出现的 appId 换成 `<APP_ID>`（这次没有出现，所以全部是 `captured`），最后再扫一遍输出目录。API 规约禁止向第三方转让、出借 appId。
- 每个文件的 URL、状态码、`RESULT.STATUS`、响应头（去掉 `Set-Cookie`）、抓取时间、字节数、SHA-256、样本类型都在 `_responses.json`。
- 完整性校验：在本目录运行 `shasum -a 256 -c SHA256SUMS`（覆盖除 README.md 外的全部文件）。
- 样本类型：`captured` = 原样保存；`sanitized` = 脱敏。

## 许可与署名

- API 利用规约：<https://www.e-stat.go.jp/api/terms-of-use>；数据本身按 e-Stat 利用规约（政府標準利用規約 第2.0版，<https://www.e-stat.go.jp/terms-of-use>），与 `jp-estat-files` 相同，先按 `internal_only` 处理。
- 出处：「出典：政府統計の総合窓口（e-Stat）」。API 规约另要求在服务上注明使用了 e-Stat API 且内容不受国家保证，301 写来源说明时一并处理。
- 内容是统计表元数据和全国汇总值，没有自然人的个人信息。

## 文件一览

| 文件名 | 类型 | 来源 URL（`https://api.e-stat.go.jp/rest/3.0/app/json/` 之后） | 字节 | 用途（测试点） |
|---|---|---|---|---|
| `getStatsList_00200524_limit5.json` | captured | `getStatsList?lang=J&statsCode=00200524&limit=5&appId=<APP_ID>` | 6,947 | 目录：人口推計共 420 张表（`NUMBER`），只回 5 条，`RESULT_INF.NEXT_KEY`=6 表示还有下一页；`STAT_NAME` 是 `{"@code","$"}` 对象 |
| `getMetaInfo_0003443838.json` | captured | `getMetaInfo?lang=J&statsDataId=0003443838&appId=<APP_ID>` | 9,566 | 表 1 维度代码（见下） |
| `getMetaInfo_0003005865.json` | captured | `getMetaInfo?lang=J&statsDataId=0003005865&appId=<APP_ID>` | 27,197 | 表 2 维度代码 |
| `getMetaInfo_0003427113.json` | captured | `getMetaInfo?lang=J&statsDataId=0003427113&appId=<APP_ID>` | 154,627 | 表 3 维度代码；品目 790 个、地域 67 个，时间轴混有年、年度、月 |
| `getStatsData_0003443838_start1_limit10.json` | captured | `getStatsData?lang=J&statsDataId=0003443838&startPosition=1&limit=10&appId=<APP_ID>` | 11,369 | 表 1 第 1 页：`TOTAL_NUMBER`=4779，`NEXT_KEY`=11；值在 `DATA_INF.VALUE[].$`（字符串），维度在 `@tab`/`@cat01..04`/`@area`/`@time`，单位 `@unit`=万人；`DATA_INF.NOTE` 给出 `***`、`-` 两个特殊符号 |
| `getStatsData_0003443838_start11_limit10.json` | captured | `getStatsData?...&startPosition=11&limit=10&appId=<APP_ID>` | 11,371 | 表 1 第 2 页：`FROM_NUMBER`=11，`NEXT_KEY`=21；与第 1 页拼起来检验分页 |

抓取时间、响应头和 SHA-256 见 `_responses.json`。

## 用 getMetaInfo 确认的维度代码（计划文档 3.1 的待核实项）

| 表 | 政府統計コード | 表章 `cdTab` | 过滤用代码 | 时间轴 |
|---|---|---|---|---|
| 1 人口推計（`0003443838`） | `00200524` | `001` 人口（万人） | `cdCat01=001` 総人口、`cdCat02=000` 男女計、`cdCat03=01000` 総数、`cdCat04=01` 概算値、`cdArea=00000` 全国 | 59 个月，`2021001212`（2021年12月）到 `2026001010`（2026年10月） |
| 2 労働力調査（`0003005865`） | `00200531` | `02` 率（％） | `cdCat01=000` 全産業；`cdCat02` 为 `01` 労働力人口 / `13` 就業 / `08` 完全失業者（三个比率）；`cdCat03=0` 総数；`cdArea=00000` | 321 个月，`2000000101` 到 `2026000909` |
| 3 消費者物価指数（`0003427113`） | `00200573`（已确认） | `1` 指数 | `cdCat01=0001`（名称「0001 総合」）；`cdArea=00000` 全国（已确认） | 793 项，按新到旧排列；月 `YYYY00MMMM`（如 `2026000909`），年 `YYYY000000`，年度 `YYYY100000`；"只取月次"要按代码形状过滤 |

时间代码统一为 10 位：年 4 位 + 2 位（`00` 年/月，`10` 年度）+ 4 位（月份重复两遍，年和年度为 `0000`）。

## 观察

- 表 1 的 `getMetaInfo.TABLE_INF.OVERALL_TOTAL_NUMBER` 是 4698，同一天 `getStatsData.RESULT_INF.TOTAL_NUMBER` 是 4779，两者不一致；分页以 `getStatsData` 的 `TOTAL_NUMBER` / `NEXT_KEY` 为准。
- 表 1 只保留最近约 5 年（59 个月），不是计划里写的 24 个月；取最近 24 个月要用 `cdTimeFrom`。
- 表 2 的元数据只说明了基准人口切换和遡及补正，没有写是原数值还是季调值，这一项仍待核实。
- 表 3 全表 13,555,226 个单元，必须带 `cdTab`、`cdCat01`、`cdArea` 过滤，不能整表取。
- 表 4（毎月勤労統計）的 statsDataId 仍未定，这次没有录。
- 各表 `OPEN_DATE` / `UPDATED_DATE`：表 1 2026-10-20，表 2 2026-10-30，表 3 2026-10-23；没有单独的发布版本号字段（ADR 0007：`release_id` 只能用来源给的东西，301 起草时再定用哪个字段）。
